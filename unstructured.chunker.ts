import type {
  ChunkerOptions,
  DocumentChunk,
  ParseResponse,
  UnstructuredElement,
} from "./unstructured.types";

const DEFAULT_TARGET = 1250;
const DEFAULT_OVERLAP = 175;
const HEADING_TYPES = new Set(["title", "header"]);

function clean(value: string): string {
  return value.replace(/\r\n/g, "\n").replace(/[ \t]+/g, " ").trim();
}

function pagesFor(element: UnstructuredElement): number[] {
  const page = element.metadata?.page_number;
  return typeof page === "number" && Number.isInteger(page) && page > 0 ? [page] : [];
}

function renderElement(element: UnstructuredElement): string {
  const text = clean(typeof element.text === "string" ? element.text : "");
  if (!text) return "";
  return String(element.type).toLowerCase() === "table" ? `\n${text}\n` : text;
}

interface Section {
  headingPath: string[];
  heading: string;
  text: string;
  pages: Set<number>;
}

/** Builds heading-aware chunks while keeping tables atomic and page references. */
export function buildSemanticChunks(
  markdown: string,
  elements: UnstructuredElement[] = [],
  options: ChunkerOptions = {},
): DocumentChunk[] {
  const target = Math.max(1, options.targetChars ?? DEFAULT_TARGET);
  const overlap = Math.max(0, Math.min(options.overlapChars ?? DEFAULT_OVERLAP, target - 1));
  const sections: Section[] = [];
  const headingStack: string[] = [];
  let current: Section = { headingPath: [], heading: "", text: "", pages: new Set() };

  const pushCurrent = () => {
    if (current.text.trim()) sections.push(current);
    current = {
      headingPath: [...headingStack],
      heading: headingStack.at(-1) ?? "",
      text: "",
      pages: new Set(),
    };
  };

  if (elements.length) {
    for (const element of elements) {
      const text = renderElement(element);
      if (!text) continue;
      const type = String(element.type ?? "").toLowerCase();
      if (HEADING_TYPES.has(type)) {
        pushCurrent();
        const level = Math.max(1, Number(element.metadata?.category_depth ?? 1) || 1);
        headingStack.splice(level - 1);
        headingStack.push(clean(text));
        current.headingPath = [...headingStack];
        current.heading = headingStack.at(-1) ?? "";
        continue;
      }
      current.text += `${current.text ? "\n\n" : ""}${text}`;
      for (const page of pagesFor(element)) current.pages.add(page);
    }
    pushCurrent();
  } else {
    for (const rawLine of markdown.replace(/\r\n/g, "\n").split("\n")) {
      const line = rawLine.trim();
      const match = /^(#{1,6})\s+(.+)$/.exec(line);
      if (match) {
        pushCurrent();
        const level = match[1].length;
        headingStack.splice(level - 1);
        headingStack.push(clean(match[2]));
        current.headingPath = [...headingStack];
        current.heading = headingStack.at(-1) ?? "";
      } else if (line) {
        current.text += `${current.text ? "\n" : ""}${line}`;
      }
    }
    pushCurrent();
  }

  const chunks: DocumentChunk[] = [];
  let chunkId = 1;
  for (const section of sections) {
    const units = section.text.split(/(?=\n\n|\n(?=\|))/g).map(clean).filter(Boolean);
    let buffer = "";
    let pages = new Set(section.pages);
    const flush = () => {
      const content = buffer.trim();
      if (!content) return;
      chunks.push({
        chunkId: chunkId++,
        heading: section.heading,
        headingPath: [...section.headingPath],
        content,
        pageNumbers: [...pages].sort((a, b) => a - b),
        charCount: content.length,
      });
      const tail = content.slice(Math.max(0, content.length - overlap));
      buffer = tail;
      pages = new Set(section.pages);
    };

    for (const unit of units) {
      const isTable = /^\|[\s\S]*\|$/.test(unit) || /^table\b/i.test(unit);
      const separator = buffer ? "\n\n" : "";
      if (buffer.length + separator.length + unit.length > target && buffer) flush();
      if (isTable && buffer && buffer.length + unit.length > target) flush();
      buffer += `${buffer ? "\n\n" : ""}${unit}`;
    }
    flush();
  }
  return chunks;
}

/**
 * Normalizes a provider-native chunk array when a Transform response/profile supplies one.
 * Current Parse and Extract contracts do not guarantee this field, so null means local
 * heading-aware chunking should be used instead.
 */
export function nativeChunksFromResponses(responses: ParseResponse[]): DocumentChunk[] | null {
  const responseWithChunks = responses.find((response) => Array.isArray(response.chunks) && response.chunks.length);
  const raw = responseWithChunks?.chunks ?? [];
  if (!raw.length) return null;

  const chunks: DocumentChunk[] = [];
  for (const [index, value] of raw.entries()) {
    if (!value || typeof value !== "object") continue;
    const item = value as Record<string, unknown>;
    const content = String(item.content ?? item.text ?? item.markdown ?? "").trim();
    if (!content) continue;
    const pagesValue = item.pageNumbers ?? item.page_numbers ?? item.pages;
    const pageNumbers = Array.isArray(pagesValue)
      ? pagesValue.map(Number).filter((page) => Number.isInteger(page) && page > 0)
      : typeof pagesValue === "number" && pagesValue > 0
        ? [pagesValue]
        : [];
    const headingPathValue = item.headingPath ?? item.heading_path;
    const headingPath = Array.isArray(headingPathValue)
      ? headingPathValue.map(String).filter(Boolean)
      : typeof item.heading === "string" && item.heading.trim()
        ? [item.heading.trim()]
        : [];
    chunks.push({
      chunkId: Number.isInteger(item.chunkId) ? Number(item.chunkId) : index + 1,
      heading: headingPath.at(-1) ?? String(item.heading ?? ""),
      headingPath,
      content,
      pageNumbers: [...new Set(pageNumbers)].sort((a, b) => a - b),
      charCount: content.length,
    });
  }
  return chunks.length ? chunks : null;
}
