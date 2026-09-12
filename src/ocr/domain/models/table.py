from typing import List, Optional
from pydantic import BaseModel, Field

class TableCell(BaseModel):
    row_idx: int
    col_idx: int
    row_span: int = 1
    col_span: int = 1
    text: str = ""
    bbox: List[float] = Field(default_factory=list)
    confidence: float = 1.0

class TableStructure(BaseModel):
    table_id: str
    page: int
    bbox: List[float] = Field(default_factory=list)
    num_rows: int = 0
    num_cols: int = 0
    cells: List[TableCell] = Field(default_factory=list)
    caption: Optional[str] = None
    confidence: float = 1.0

    def to_markdown(self) -> str:
        if self.num_rows == 0 or self.num_cols == 0:
            return ""

        # Build grid
        grid = [["" for _ in range(self.num_cols)] for _ in range(self.num_rows)]
        for cell in self.cells:
            if 0 <= cell.row_idx < self.num_rows and 0 <= cell.col_idx < self.num_cols:
                clean_text = cell.text.replace("\n", " ").replace("|", "\\|").strip()
                grid[cell.row_idx][cell.col_idx] = clean_text

        lines = []
        if self.caption:
            lines.append(f"**{self.caption}**\n")

        # Header row
        header = grid[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * self.num_cols) + " |")

        # Data rows
        for r in range(1, self.num_rows):
            lines.append("| " + " | ".join(grid[r]) + " |")

        return "\n".join(lines)
