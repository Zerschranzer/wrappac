import json
from pathlib import Path
from typing import List


class SearchHistory:
    def __init__(self, max_items: int = 20):
        self.max_items = max_items
        self.config_file = Path.home() / ".config" / "wrappac" / "search_history.json"
        self.history: List[str] = []
        self.load()

    def load(self):
        if self.config_file.exists():
            try:
                with open(self.config_file) as f:
                    data = json.load(f)
                    self.history = data.get("searches", [])
            except Exception:
                self.history = []

    def save(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump({"searches": self.history}, f)

    def add(self, query: str):
        query = query.strip()
        if not query or len(query) < 2:
            return
        if query in self.history:
            self.history.remove(query)
        self.history.insert(0, query)
        self.history = self.history[:self.max_items]
        self.save()

    def get_all(self) -> List[str]:
        return list(self.history)
