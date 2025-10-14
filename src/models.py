from dataclasses import dataclass
import re
from typing import List
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex

from i18n import tr

@dataclass
class PackageItem:
    pid: str           # pacman: package name, flatpak: app ID
    name: str          # Display name
    version: str
    source: str        # "Repo" | "AUR" | "Flatpak"
    origin: str        # Repository or remote (e.g. extra, community, local, flathub)
    size: str = ""

class PackageModel(QAbstractTableModel):
    headers = ["Name", "Version", "Size", "Quelle", "Origin/Repo", "ID"]

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
            2: 'size',      # Size
            3: 'source',    # Source
            4: 'origin',    # Origin/Repo
            5: 'pid'        # ID
        }

        reverse = (self._sort_order == Qt.DescendingOrder)

        def _sort_key(item: PackageItem):
            if self._sort_column == 2:
                return self._size_to_bytes(item.size)
            attr = attr_map.get(self._sort_column, 'name')
            value = getattr(item, attr, '')
            if isinstance(value, str):
                return value.lower()
            return value

        self._filtered.sort(key=_sort_key, reverse=reverse)

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

    def total_count(self) -> int:
        return len(self._all)

    def filtered_count(self) -> int:
        return len(self._filtered)

    def all_items(self) -> List[PackageItem]:
        return list(self._all)

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
        values = [
            it.name,
            it.version,
            it.size or "",
            it.source,
            it.origin,
            it.pid,
        ]
        return values[col]

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            labels = [
                tr("table_package"),
                tr("table_version"),
                tr("table_size"),
                tr("table_source"),
                tr("table_remote_source"),
                "ID",
            ]
            if 0 <= section < len(labels):
                return labels[section]
            return self.headers[section]
        return None

    def item_at(self, row: int) -> PackageItem:
        return self._filtered[row]

    @staticmethod
    def _size_to_bytes(size: str) -> float:
        if not size:
            return 0.0
        match = re.match(r"([0-9.,]+)\s*([KMGTPE]?i?B)?", size.strip())
        if not match:
            return 0.0
        number_part = match.group(1).replace(',', '.')
        try:
            value = float(number_part)
        except ValueError:
            return 0.0
        unit = (match.group(2) or '').upper()
        factors = {
            'B': 1,
            'KIB': 1024,
            'MIB': 1024 ** 2,
            'GIB': 1024 ** 3,
            'TIB': 1024 ** 4,
            'PIB': 1024 ** 5,
            'KB': 1000,
            'MB': 1000 ** 2,
            'GB': 1000 ** 3,
            'TB': 1000 ** 4,
            'PB': 1000 ** 5,
        }
        multiplier = factors.get(unit, 1)
        return value * multiplier
