import type { DiagramRenderer } from "../../mermaid/render.js";
import type { ContentEnhancer } from "./content-enhancer.js";

export declare function createLessonDiagramEnhancer(
    renderer: DiagramRenderer,
): ContentEnhancer;
