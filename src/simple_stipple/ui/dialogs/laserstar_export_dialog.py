"""Single-sheet LaserStar operator package configuration."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.layout import (
    sep,
    surface_frame,
)
from simple_stipple.ui.components.tokens import (
    SPACE_LG,
    SPACE_MD,
)


class LaserStarExportDialog(QDialog):
    """Collect and validate all package metadata in one transaction."""

    def __init__(
        self,
        *,
        job_name: str,
        destination: str,
        has_engraving: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create LaserStar Operator Package")
        self.setModal(True)
        self.resize(620, 430)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        root.setSpacing(SPACE_MD)
        title = QLabel("LaserStar operator package")
        title.setProperty("role", "page-title")
        root.addWidget(title)
        subtitle = QLabel(
            "Review registration and operator metadata, then create all job assets together."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        card = surface_frame("panel")
        form = QFormLayout(card)
        self.job_name = QLineEdit(job_name)
        self.job_name.setAccessibleName("Operator job name")
        form.addRow("Job name", self.job_name)
        destination_row = QWidget()
        destination_layout = QHBoxLayout(destination_row)
        destination_layout.setContentsMargins(0, 0, 0, 0)
        destination_layout.setSpacing(SPACE_MD)
        self.destination = QLineEdit(destination)
        self.destination.setAccessibleName("Package destination folder")
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        destination_layout.addWidget(self.destination, 1)
        destination_layout.addWidget(browse)
        form.addRow("Destination", destination_row)
        self.machine = QComboBox()
        self.machine.addItem("LaserStar 3602XL", "laserstar-3602xl")
        form.addRow("Machine", self.machine)
        self.material = QComboBox()
        self.material.addItems(["Unspecified — operator verifies", "Polymer", "Aluminum", "Steel"])
        form.addRow("Material", self.material)
        self.registration = QLabel("Millimeters · preserved drawing origin")
        self.registration.setProperty("role", "hint-sm")
        form.addRow("Units / origin", self.registration)
        contents = "FVI vectors + setup sheet + checklist + preview"
        if has_engraving:
            contents += " + positioned engraving assets"
        self.contents = QLabel(contents)
        self.contents.setWordWrap(True)
        form.addRow("Package contents", self.contents)
        root.addWidget(card)

        self.validation = QLabel("")
        self.validation.setProperty("role", "status-err")
        self.validation.setWordWrap(True)
        root.addWidget(self.validation)
        sep(root)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        create = QPushButton("Create Package")
        create.setProperty("role", "primary")
        create.setDefault(True)
        create.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel)
        buttons.addWidget(create)
        root.addLayout(buttons)
        install_dialog_focus_lifecycle(self, self.job_name)

        self.job_name.textChanged.connect(self._clear_validation)
        self.destination.textChanged.connect(self._clear_validation)

    def _clear_validation(self) -> None:
        if self.validation.text():
            self.validation.setText("")
            self.validation.setAccessibleDescription("")

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose package destination", self.destination.text() or str(Path.home())
        )
        if selected:
            self.destination.setText(selected)

    def _accept_if_valid(self) -> None:
        errors: list[str] = []
        if not self.job_name.text().strip():
            errors.append("Enter a job name.")
        destination = Path(self.destination.text().strip()).expanduser()
        if not self.destination.text().strip():
            errors.append("Choose a destination folder.")
        elif destination.exists() and not destination.is_dir():
            errors.append("Destination must be a folder.")
        if errors:
            self.validation.setText(" ".join(errors))
            self.validation.setAccessibleDescription(self.validation.text())
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "job_name": self.job_name.text().strip(),
            "destination": str(Path(self.destination.text().strip()).expanduser()),
            "machine": str(self.machine.currentData()),
            "material": self.material.currentText(),
        }
