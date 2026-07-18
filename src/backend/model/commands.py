"""Immutable, serializable, reversible document command values."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar, TypeAlias, cast

Point = tuple[float, float]
FrozenValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["FrozenValue", ...]
    | tuple[tuple[str, "FrozenValue"], ...]
)


def freeze_value(value: Any) -> FrozenValue:
    if isinstance(value, dict):
        return tuple((str(key), freeze_value(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"Command payload values must be serializable, got {type(value).__name__}")


def _thaw(value: FrozenValue) -> Any:
    if isinstance(value, tuple):
        if all(
            isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
            for item in value
        ):
            items = cast(tuple[tuple[str, FrozenValue], ...], value)
            return {key: _thaw(item) for key, item in items}
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    """Serializable immutable representation of an entity at a point in time."""

    id: str
    points: tuple[Point, ...]
    kind: str = "polyline"
    meta: FrozenValue = None
    construction: bool = False
    hidden: bool = False
    locked: bool = False
    group: int | None = None
    layer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(tuple(point) for point in self.points))
        object.__setattr__(self, "meta", freeze_value(self.meta))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "meta": _thaw(self.meta)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EntitySnapshot:
        return cls(**{**value, "points": tuple(tuple(point) for point in value["points"])})

    @classmethod
    def capture(cls, entity: Any) -> EntitySnapshot:
        """Capture a runtime entity without coupling command values to its class."""
        return cls(
            id=entity.id,
            points=tuple(entity.points),
            kind=entity.kind,
            meta=freeze_value(entity.meta),
            construction=entity.construction,
            hidden=entity.hidden,
            locked=entity.locked,
            group=entity.group,
            layer=entity.layer,
        )


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    """Serializable immutable snapshot for aggregate-level state changes."""

    entities: tuple[EntitySnapshot, ...] = ()
    selection_ids: tuple[str, ...] = ()
    layer_order: tuple[str, ...] = ()
    active_layer: str | None = None
    layer_colors: tuple[tuple[str, str], ...] = ()
    group_labels: tuple[tuple[int, str], ...] = ()
    next_group_id: int = 0
    constraints: tuple[FrozenValue, ...] = ()

    @classmethod
    def capture(cls, document: Any) -> DocumentSnapshot:
        selected = document.selected_ids()
        return cls(
            entities=tuple(EntitySnapshot.capture(entity) for entity in document.entities),
            selection_ids=tuple(entity.id for entity in document.entities if entity.id in selected),
            layer_order=tuple(document.layer_order),
            active_layer=document.active_layer,
            layer_colors=tuple(sorted(document.layer_colors.items())),
            group_labels=tuple(sorted(document.group_labels.items())),
            next_group_id=document.next_group_id,
            constraints=tuple(freeze_value(item.to_dict()) for item in document.constraints),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [entity.to_dict() for entity in self.entities],
            "selection_ids": list(self.selection_ids),
            "layer_order": list(self.layer_order),
            "active_layer": self.active_layer,
            "layer_colors": [list(item) for item in self.layer_colors],
            "group_labels": [list(item) for item in self.group_labels],
            "next_group_id": self.next_group_id,
            "constraints": [_thaw(item) for item in self.constraints],
        }

    def constraint_dicts(self) -> tuple[dict[str, Any], ...]:
        return tuple(item for value in self.constraints if isinstance((item := _thaw(value)), dict))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DocumentSnapshot:
        return cls(
            entities=tuple(EntitySnapshot.from_dict(item) for item in value.get("entities", ())),
            selection_ids=tuple(value.get("selection_ids", ())),
            layer_order=tuple(value.get("layer_order", ())),
            active_layer=value.get("active_layer"),
            layer_colors=tuple(
                (str(key), str(color)) for key, color in value.get("layer_colors", ())
            ),
            group_labels=tuple(
                (int(key), str(label)) for key, label in value.get("group_labels", ())
            ),
            next_group_id=int(value.get("next_group_id", 0)),
            constraints=tuple(freeze_value(item) for item in value.get("constraints", ())),
        )


@dataclass(frozen=True, slots=True)
class Command:
    """Base command with schema-versioned tagged serialization."""

    schema_version: int = 1
    command_type: ClassVar[str] = "command"

    def reverse(self) -> Command:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for field_name in ("entities", "before", "after"):
            snapshots = getattr(self, field_name, None)
            if snapshots is not None:
                payload[field_name] = [snapshot.to_dict() for snapshot in snapshots]
        return {"type": self.command_type, **payload}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Command:
        command_type = value.get("type")
        command_class = _COMMAND_TYPES.get(str(command_type))
        if command_class is None:
            raise ValueError(f"Unknown command type: {command_type}")
        payload = {key: item for key, item in value.items() if key != "type"}
        return command_class._from_payload(payload)

    @classmethod
    def _from_payload(cls, value: dict[str, Any]) -> Command:
        names = {item.name for item in fields(cls)}
        payload = {key: item for key, item in value.items() if key in names}
        for name in ("entity_ids", "previous_ids"):
            if name in payload:
                payload[name] = tuple(payload[name])
        if "origin" in payload:
            payload["origin"] = tuple(payload["origin"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class EntityChangeCommand(Command):
    """Base for topology commands whose inverse restores captured entities."""

    entity_ids: tuple[str, ...] = ()
    before: tuple[EntitySnapshot, ...] = ()
    after: tuple[EntitySnapshot, ...] = ()

    def reverse(self) -> EntityChangeCommand:
        return RestoreEntitiesCommand(
            entity_ids=tuple(entity.id for entity in self.after),
            before=self.after,
            after=self.before,
        )

    @classmethod
    def _from_payload(cls, value: dict[str, Any]) -> EntityChangeCommand:
        names = {item.name for item in fields(cls)}
        payload = {key: item for key, item in value.items() if key in names}
        payload["schema_version"] = int(value.get("schema_version", 1))
        payload["entity_ids"] = tuple(value.get("entity_ids", ()))
        payload["before"] = tuple(
            EntitySnapshot.from_dict(item) for item in value.get("before", ())
        )
        payload["after"] = tuple(EntitySnapshot.from_dict(item) for item in value.get("after", ()))
        if "cutter" in payload:
            payload["cutter"] = tuple(tuple(point) for point in payload["cutter"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class RestoreEntitiesCommand(EntityChangeCommand):
    command_type: ClassVar[str] = "restore_entities"


@dataclass(frozen=True, slots=True)
class UpdateEntitiesCommand(EntityChangeCommand):
    """Replace existing entities with immutable edited snapshots."""

    command_type: ClassVar[str] = "update_entities"


@dataclass(frozen=True, slots=True)
class SplitCommand(EntityChangeCommand):
    command_type: ClassVar[str] = "split"
    cutter: tuple[Point, ...] = ()

    def reverse(self) -> Command:
        return RestoreEntitiesCommand(
            entity_ids=tuple(entity.id for entity in self.after),
            before=self.after,
            after=self.before,
        )


@dataclass(frozen=True, slots=True)
class BooleanOpCommand(EntityChangeCommand):
    command_type: ClassVar[str] = "boolean"
    operation: str = "union"


@dataclass(frozen=True, slots=True)
class TransformCommand(Command):
    command_type: ClassVar[str] = "transform"
    entity_ids: tuple[str, ...] = ()
    operation: str = "translate"
    origin: Point = (0.0, 0.0)
    x: float = 0.0
    y: float = 0.0

    def reverse(self) -> TransformCommand:
        if self.operation == "translate":
            return TransformCommand(
                entity_ids=self.entity_ids,
                operation=self.operation,
                origin=self.origin,
                x=-self.x,
                y=-self.y,
            )
        if self.operation == "rotate":
            return TransformCommand(
                entity_ids=self.entity_ids, operation=self.operation, origin=self.origin, x=-self.x
            )
        if self.operation == "scale":
            if abs(self.x) < 1e-12 or abs(self.y) < 1e-12:
                raise ValueError("A zero scale is not reversible")
            return TransformCommand(
                entity_ids=self.entity_ids,
                operation=self.operation,
                origin=self.origin,
                x=1.0 / self.x,
                y=1.0 / self.y,
            )
        if self.operation == "mirror":
            return self
        raise ValueError(f"Unknown transform: {self.operation}")


@dataclass(frozen=True, slots=True)
class MoveEntityCommand(Command):
    command_type: ClassVar[str] = "move_entity"
    entity_ids: tuple[str, ...] = ()
    dx: float = 0.0
    dy: float = 0.0

    def reverse(self) -> MoveEntityCommand:
        return MoveEntityCommand(entity_ids=self.entity_ids, dx=-self.dx, dy=-self.dy)


@dataclass(frozen=True, slots=True)
class SelectCommand(Command):
    command_type: ClassVar[str] = "select"
    entity_ids: tuple[str, ...] = ()
    previous_ids: tuple[str, ...] = ()

    def reverse(self) -> SelectCommand:
        return SelectCommand(entity_ids=self.previous_ids, previous_ids=self.entity_ids)


@dataclass(frozen=True, slots=True)
class CreateCommand(Command):
    command_type: ClassVar[str] = "create"
    entities: tuple[EntitySnapshot, ...] = ()
    positions: tuple[int, ...] = ()

    def reverse(self) -> DeleteCommand:
        return DeleteCommand(
            entity_ids=tuple(entity.id for entity in self.entities),
            entities=self.entities,
            positions=self.positions,
        )

    @classmethod
    def _from_payload(cls, value: dict[str, Any]) -> CreateCommand:
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            entities=tuple(EntitySnapshot.from_dict(item) for item in value.get("entities", ())),
            positions=tuple(int(item) for item in value.get("positions", ())),
        )


@dataclass(frozen=True, slots=True)
class DeleteCommand(Command):
    command_type: ClassVar[str] = "delete"
    entity_ids: tuple[str, ...] = ()
    entities: tuple[EntitySnapshot, ...] = ()
    positions: tuple[int, ...] = ()

    def reverse(self) -> CreateCommand:
        return CreateCommand(entities=self.entities, positions=self.positions)

    @classmethod
    def _from_payload(cls, value: dict[str, Any]) -> DeleteCommand:
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            entity_ids=tuple(value.get("entity_ids", ())),
            entities=tuple(EntitySnapshot.from_dict(item) for item in value.get("entities", ())),
            positions=tuple(int(item) for item in value.get("positions", ())),
        )


@dataclass(frozen=True, slots=True)
class ReplaceDocumentCommand(Command):
    """Replace aggregate state atomically for layer/group and compound edits."""

    command_type: ClassVar[str] = "replace_document"
    before_document: DocumentSnapshot = DocumentSnapshot()
    after_document: DocumentSnapshot = DocumentSnapshot()

    def reverse(self) -> ReplaceDocumentCommand:
        return ReplaceDocumentCommand(
            before_document=self.after_document,
            after_document=self.before_document,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.command_type,
            "schema_version": self.schema_version,
            "before_document": self.before_document.to_dict(),
            "after_document": self.after_document.to_dict(),
        }

    @classmethod
    def _from_payload(cls, value: dict[str, Any]) -> ReplaceDocumentCommand:
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            before_document=DocumentSnapshot.from_dict(value.get("before_document", {})),
            after_document=DocumentSnapshot.from_dict(value.get("after_document", {})),
        )


@dataclass(frozen=True, slots=True)
class ResampleCommand(EntityChangeCommand):
    command_type: ClassVar[str] = "resample"
    value: float = 0.0
    by_count: bool = False


@dataclass(frozen=True, slots=True)
class MergeCommand(EntityChangeCommand):
    command_type: ClassVar[str] = "merge"


@dataclass(frozen=True, slots=True)
class ExplodeCommand(EntityChangeCommand):
    command_type: ClassVar[str] = "explode"


_COMMAND_TYPES: dict[str, type[Command]] = {
    command.command_type: command
    for command in (
        RestoreEntitiesCommand,
        UpdateEntitiesCommand,
        SplitCommand,
        BooleanOpCommand,
        TransformCommand,
        MoveEntityCommand,
        SelectCommand,
        CreateCommand,
        DeleteCommand,
        ReplaceDocumentCommand,
        ResampleCommand,
        MergeCommand,
        ExplodeCommand,
    )
}
