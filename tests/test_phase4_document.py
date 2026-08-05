"""Phase 4.1 — the Document holds placed images and region treatments.

Both used to belong to the Pattern page, which is why Trace and Pattern could
not know that the image you traced and the image you are engraving are the
same picture in the same place.
"""

from __future__ import annotations

from simple_stipple.document.model import Document, EntityRecord, PlacedImage

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]


def test_a_placed_image_round_trips_through_a_dict() -> None:
    image = PlacedImage(
        path="/tmp/logo.png",
        x=1.5,
        y=2.5,
        width=20.0,
        height=10.0,
        rotation=15.0,
        options={"max_power_percent": 80},
    )
    restored = PlacedImage.from_dict(image.to_dict())
    assert restored == image


def test_a_malformed_placement_degrades_to_zero_rather_than_raising() -> None:
    restored = PlacedImage.from_dict({"path": "a.png", "x": "nonsense", "width": None})
    assert (restored.x, restored.width) == (0.0, 0.0)
    assert restored.path == "a.png"
    assert restored.id


def test_the_document_carries_images_and_treatments() -> None:
    entity = EntityRecord(points=list(SQUARE))
    document = Document(entities=[entity])
    document.images.append(PlacedImage(path="logo.png", width=5.0, height=5.0))

    document.set_treatment(entity.id, {"kind": "pattern", "pattern": "Honeycomb"})
    assert document.treatment_for(entity.id)["pattern"] == "Honeycomb"
    assert document.image_for_id(document.images[0].id) is document.images[0]

    # A treatment is stored by value, not by reference.
    treatment = {"kind": "cut"}
    document.set_treatment(entity.id, treatment)
    treatment["kind"] = "mangled"
    assert document.treatment_for(entity.id)["kind"] == "cut"


def test_setting_a_none_treatment_clears_it() -> None:
    entity = EntityRecord(points=list(SQUARE))
    document = Document(entities=[entity])
    document.set_treatment(entity.id, {"kind": "cut"})
    document.set_treatment(entity.id, {"kind": "none"})
    assert document.treatments == {}
    document.set_treatment(entity.id, None)
    assert document.treatments == {}


def test_treatments_are_pruned_with_the_geometry_they_describe() -> None:
    keep = EntityRecord(points=list(SQUARE))
    gone = EntityRecord(points=list(SQUARE))
    document = Document(entities=[keep, gone])
    document.set_treatment(keep.id, {"kind": "cut"})
    document.set_treatment(gone.id, {"kind": "cut"})

    document.entities.remove(gone)
    assert document.prune_treatments() == 1
    assert set(document.treatments) == {keep.id}
    assert document.prune_treatments() == 0
