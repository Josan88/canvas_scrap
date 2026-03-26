from collections import defaultdict
from typing import DefaultDict, Dict, List


class SummaryCollector:
    """Collects a per-course summary of updated/created files grouped by destination folder label."""

    def __init__(self):
        # Structure: { course_name: { dest_label: [ (filename, action) ] } }
        self.per_course: Dict[str, DefaultDict[str, List[tuple]]] = {}
        self.downloaded_pdfs: List[str] = []

    def add_file(self, course_name: str, dest_label: str, filename: str, action: str):
        if not course_name or not dest_label or not filename:
            return
        if course_name not in self.per_course:
            self.per_course[course_name] = defaultdict(list)
        self.per_course[course_name][dest_label].append((filename, action))

    def has_changes(self) -> bool:
        return any(self.per_course.get(c) for c in self.per_course)

    def get_action_count(self, action: str) -> int:
        count = 0
        for folders in self.per_course.values():
            for items in folders.values():
                count += sum(1 for _, item_action in items if item_action == action)
        return count

    def print_summary(self):
        print("\n=== Summary of Updates ===")
        if not self.has_changes():
            print("No files or folders were updated across the selected courses.")
            return
        for course_name, folders in self.per_course.items():
            print(f"\nCourse: {course_name}")
            for dest_label, items in folders.items():
                print(f"  Folder: {dest_label}")
                for filename, action in items:
                    print(f"    - {filename}  [{action}]")
        print("\n==========================")
