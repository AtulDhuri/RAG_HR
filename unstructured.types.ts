export type UnstructuredElementType =
  | "Title"
  | "Header"
  | "Footer"
  | "NarrativeText"
  | "Text"
  | "ListItem"
  | "Table"
  | "Image"
  | "FigureCaption"
  | "Address"
  | "EmailAddress"
  | "PageBreak"
  | string;

export interface UnstructuredElementMetadata {
  page_number?: number;
  filename?: string;
  filetype?: string;
  languages?: string[];
  parent_id?: string;
  category_depth?: number;
  coordinates?: unknown;
  [key: string]: unknown;
}

export interface UnstructuredElement {
  type?: UnstructuredElementType;
  element_id?: string;
  text?: string;
  metadata?: UnstructuredElementMetadata;
  [key: string]: unknown;
}

export interface UploadResponse {
  file_id: string;
  expires_at: string;
  mimetype: string;
  [key: string]: unknown;
}

export interface ParseSource {
  file_id?: string;
  filename?: string;
  mimetype?: string;
  expires_at?: string;
  [key: string]: unknown;
}

export interface ParseWarning {
  code?: string;
  message?: string;
  [key: string]: unknown;
}

export interface ParseResponse {
  id?: string;
  markdown?: string | null;
  elements?: UnstructuredElement[] | null;
  metadata?: Record<string, unknown>;
  source?: ParseSource;
  title?: string;
  warnings?: ParseWarning[];
  failed_pages?: Array<Record<string, unknown>>;
  degraded_pages?: Array<Record<string, unknown>>;
  /** Forward-compatible native chunk output, when a Transform profile exposes it. */
  chunks?: unknown[] | null;
  [key: string]: unknown;
}

export interface DocumentChunk {
  chunkId: number;
  heading: string;
  headingPath: string[];
  content: string;
  pageNumbers: number[];
  charCount: number;
}

export interface ParsedPdfDocument {
  documentId: string;
  title?: string;
  totalPages: number;
  markdown: string;
  elements: UnstructuredElement[];
  headings: string[];
  tables: UnstructuredElement[];
  metadata: Record<string, unknown>;
  source?: ParseSource;
  chunks: DocumentChunk[];
}

export interface UnstructuredServiceOptions {
  apiKey?: string;
  baseUrl?: string;
  timeoutMs?: number;
  retries?: number;
  logger?: UnstructuredLogger;
}

export interface UnstructuredLogger {
  debug?(message: string, context?: Record<string, unknown>): void;
  info?(message: string, context?: Record<string, unknown>): void;
  warn?(message: string, context?: Record<string, unknown>): void;
  error?(message: string, context?: Record<string, unknown>): void;
}

export type UploadInput =
  | Blob
  | ArrayBuffer
  | Uint8Array
  | { data: Blob | ArrayBuffer | Uint8Array; filename: string; contentType?: string };

export interface ChunkerOptions {
  targetChars?: number;
  overlapChars?: number;
}
