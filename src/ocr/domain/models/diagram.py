from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class DiagramNode(BaseModel):
    node_id: str
    label: str
    bbox: List[float] = Field(default_factory=list)
    level: int = 0
    parent_id: Optional[str] = None

class DiagramEdge(BaseModel):
    source_id: str
    target_id: str
    label: Optional[str] = None

class DiagramStructure(BaseModel):
    diagram_id: str
    title: Optional[str] = None
    diagram_type: str = "ORGANIZATION_CHART"  # ORGANIZATION_CHART, FLOWCHART, ARCHITECTURE
    nodes: List[DiagramNode] = Field(default_factory=list)
    edges: List[DiagramEdge] = Field(default_factory=list)
    image_asset_path: Optional[str] = None
    caption: Optional[str] = None

    def to_mermaid(self) -> str:
        """Serializes the diagram into a valid, beautiful Mermaid.js graph TD block."""
        if not self.nodes:
            return ""

        lines = ["```mermaid", "graph TD"]
        
        # Style definition
        lines.append("    %% Styling classes")
        lines.append("    classDef lead fill:#1F4E78,stroke:#0D233A,stroke-width:2px,color:#FFFFFF,font-weight:bold;")
        lines.append("    classDef exec fill:#2E75B6,stroke:#1F4E78,stroke-width:1.5px,color:#FFFFFF;")
        lines.append("    classDef dept fill:#D9EAF7,stroke:#2E75B6,stroke-width:1px,color:#1F4E78;")
        lines.append("    classDef unit fill:#E2F0D9,stroke:#548235,stroke-width:1px,color:#274E13;")

        # Nodes
        for node in self.nodes:
            clean_label = node.label.replace('"', '\\"').replace("\n", " ").strip()
            # Choose shape and class based on hierarchy level
            if node.level <= 1:
                shape = f'["{clean_label}"]'
                style_class = "lead"
            elif node.level == 2:
                shape = f'["{clean_label}"]'
                style_class = "exec"
            elif node.level == 3:
                shape = f'["{clean_label}"]'
                style_class = "dept"
            else:
                shape = f'["{clean_label}"]'
                style_class = "unit"

            lines.append(f'    {node.node_id}{shape}:::{style_class}')

        lines.append("")
        # Edges
        for edge in self.edges:
            if edge.label:
                lines.append(f'    {edge.source_id} -->|"{edge.label}"| {edge.target_id}')
            else:
                lines.append(f'    {edge.source_id} --> {edge.target_id}')

        lines.append("```")
        return "\n".join(lines)
