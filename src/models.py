from dataclasses import dataclass
from typing import List
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex

@dataclass
class PackageItem:
    pid: str           # pacman: package name, flatpak: app ID
    name: str          # Display name
    version: str
    source: str        # "Repo" | "AUR" | "Flatpak"
    origin: str        # Repository or remote (e.g. extra, community, local, flathub)

class PackageModel(QAbstractTableModel):
    headers = ["Name", "Version", "Quelle", "Origin/Repo", "ID"]

    def __init__(self, items: List[PackageItem] | None = None):
        super().__init__()
        self._all: List[PackageItem] = items or []
        self._filtered: List[PackageItem] = list(self._all)
        self._text_filter = ""
        self._source_filter = "Alle"
        self._sort_column = 0
        self._sort_order = Qt.AscendingOrder

    def set_items(self, items: List[PackageItem]):
        self.beginResetModel()
        self._all = list(items)
        self._apply_filters()
        self._apply_sort()
        self.endResetModel()

    def _apply_filters(self):
        t = self._text_filter.lower()
        src = self._source_filter
        def ok(it: PackageItem) -> bool:
            if src != "Alle" and it.source != src:
                return False
            if not t:
                return True
            return (t in it.name.lower()) or (t in it.pid.lower())
        self._filtered = [it for it in self._all if ok(it)]

    def _apply_sort(self):
        """Sort the filtered list according to the selected column."""
        if not self._filtered:
            return

        # Map column indices to dataclass attributes
        attr_map = {
            0: 'name',      # Name
            1: 'version',   # Version
            2: 'source',    # Source
            3: 'origin',    # Origin/Repo
            4: 'pid'        # ID
        }

        attr = attr_map.get(self._sort_column, 'name')
        reverse = (self._sort_order == Qt.DescendingOrder)

        # Perform the sort; use case-insensitive comparisons for strings
        self._filtered.sort(
            key=lambda item: getattr(item, attr, '').lower() if isinstance(getattr(item, attr, ''), str) else getattr(item, attr, ''),
            reverse=reverse
        )

    def set_text_filter(self, text: str):
        self._text_filter = text
        self.beginResetModel()
        self._apply_filters()
        self._apply_sort()
        self.endResetModel()

    def set_source_filter(self, src: str):
        self._source_filter = src
        self.beginResetModel()
        self._apply_filters()
        self._apply_sort()
        self.endResetModel()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        """Implement sorting support for QTableView."""
        if column < 0 or column >= len(self.headers):
            return

        self.layoutAboutToBeChanged.emit()
        self._sort_column = column
        self._sort_order = order
        self._apply_sort()
        self.layoutChanged.emit()

    # Qt model impl
    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        it = self._filtered[index.row()]
        col = index.column()
        return [it.name, it.version, it.source, it.origin, it.pid][col]

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def item_at(self, row: int) -> PackageItem:
        return self._filtered[row]
