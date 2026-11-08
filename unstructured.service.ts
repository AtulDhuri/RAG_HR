import { buildSemanticChunks, nativeChunksFromResponses } from "./unstructured.chunker";
import type {
  ChunkerOptions,
  ParseResponse,
  ParsedPdfDocument,
  UnstructuredElement,
  UnstructuredLogger,
  UnstructuredServiceOptions,
  UploadInput,
  UploadResponse,
} from "./unstructured.types";

const DEFAULT_BASE_URL = "https://transform.unstructured.io/api/v2";
const DEFAULT_TIMEOUT_MS = 120_000;
const DEFAULT_RETRIES = 3;

export class UnstructuredError extends Error {
  constructor(
    message: string,
    public readonly code:
      | "MISSING_API_KEY"
      | "UPLOAD_FAILED"
      | "PARSE_FAILED"
      | "FILE_ID_EXPIRED"
      | "INVALID_PDF"
      | "NETWORK_TIMEOUT",
    public readonly status?: number,
    public readonly cause?: unknown,
  ) {
    super(message);
    this.name = "UnstructuredError";
  }
}

const defaultLogger: UnstructuredLogger = {
  info: (message, context) => console.info(`[unstructured] ${message}`, context ?? ""),
  warn: (message, context) => console.warn(`[unstructured] ${message}`, context ?? ""),
  error: (message, context) => console.error(`[unstructured] ${message}`, context ?? ""),
};

function jsonObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function parseResponse(value: unknown): ParseResponse {
  return jsonObject(value) as ParseResponse;
}

function asElements(value: unknown): UnstructuredElement[] {
  return Array.isArray(value) ? (value as UnstructuredElement[]) : [];
}

function maxPage(elements: UnstructuredElement[]): number {
  return elements.reduce((max, element) => {
    const page = element.metadata?.page_number;
    return typeof page === "number" && Number.isFinite(page) ? Math.max(max, page) : max;
  }, 0);
}

function titleFrom(elements: UnstructuredElement[], parsed: ParseResponse): string | undefined {
  if (typeof parsed.title === "string" && parsed.title.trim()) return parsed.title.trim();
  const title = elements.find((element) => String(element.type).toLowerCase() === "title");
  return typeof title?.text === "string" && title.text.trim() ? title.text.trim() : undefined;
}

export class UnstructuredService {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly retries: number;
  private readonly logger: UnstructuredLogger;

  constructor(options: UnstructuredServiceOptions = {}) {
    this.apiKey = (options.apiKey ?? this.readApiKey()).trim();
    if (!this.apiKey) throw new UnstructuredError("UNSTRUCTURED_API_KEY is not configured", "MISSING_API_KEY");
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.retries = Math.max(0, options.retries ?? DEFAULT_RETRIES);
    this.logger = options.logger ?? defaultLogger;
  }

  /** Uploads the original PDF and returns the temporary file reference. */
  async upload(input: UploadInput): Promise<UploadResponse> {
    const form = new FormData();
    const file = this.toBlob(input);
    const filename = this.filename(input);
    form.append("file", file, filename);
    this.logger.info?.("Uploading PDF", { filename });
    const response = await this.request("/upload", { method: "POST", body: form }, "UPLOAD_FAILED");
    const payload = jsonObject(response);
    if (typeof payload.file_id !== "string" || !payload.file_id) {
      throw new UnstructuredError("Upload response did not contain file_id", "UPLOAD_FAILED");
    }
    return payload as UploadResponse;
  }

  /** Parses an uploaded file ID twice when necessary so callers receive both representations. */
  async parseUploadedFile(
    fileId: string,
    chunkerOptions: ChunkerOptions = {},
  ): Promise<ParsedPdfDocument> {
    if (!fileId.trim()) throw new UnstructuredError("file_id is required", "PARSE_FAILED");
    this.logger.info?.("Parsing uploaded PDF", { fileId });
    const [markdownResult, elementsResult] = await Promise.all([
      this.parse(fileId),
      this.parse(fileId, "elements"),
    ]);
    const markdown = typeof markdownResult.markdown === "string" ? markdownResult.markdown : "";
    const elements = asElements(elementsResult.elements ?? markdownResult.elements);
    const metadata = { ...(markdownResult.metadata ?? {}), ...(elementsResult.metadata ?? {}) };
    const pages = maxPage(elements);
    const metadataPageCount = Number(metadata.page_count ?? metadata.pageCount ?? 0);
    const totalPages = pages || (Number.isFinite(metadataPageCount) ? metadataPageCount : 0);
    const nativeChunks = nativeChunksFromResponses([markdownResult, elementsResult]);
    const chunks = nativeChunks ?? buildSemanticChunks(markdown, elements, chunkerOptions);
    if (nativeChunks) {
      this.logger.info?.("Using native Unstructured chunks", { count: nativeChunks.length });
    } else {
      this.logger.debug?.("No native Unstructured chunks returned; using local chunker");
    }
    return {
      documentId: String(elementsResult.id ?? markdownResult.id ?? fileId),
      title: titleFrom(elements, elementsResult) ?? titleFrom(elements, markdownResult),
      totalPages,
      markdown,
      elements,
      headings: elements
        .filter((element) => ["title", "header"].includes(String(element.type).toLowerCase()))
        .map((element) => String(element.text ?? "").trim())
        .filter(Boolean),
      tables: elements.filter((element) => String(element.type).toLowerCase() === "table"),
      metadata: {
        ...metadata,
        warnings: [...(markdownResult.warnings ?? []), ...(elementsResult.warnings ?? [])],
        failed_pages: markdownResult.failed_pages ?? elementsResult.failed_pages ?? [],
        degraded_pages: markdownResult.degraded_pages ?? elementsResult.degraded_pages ?? [],
        chunking: nativeChunks ? "unstructured" : "local-heading-aware",
      },
      source: elementsResult.source ?? markdownResult.source,
      chunks,
    };
  }

  /** Uploads and parses a PDF in one reusable operation. */
  async parsePdf(input: UploadInput, chunkerOptions: ChunkerOptions = {}): Promise<ParsedPdfDocument> {
    const uploaded = await this.upload(input);
    try {
      return await this.parseUploadedFile(uploaded.file_id, chunkerOptions);
    } catch (error) {
      if (error instanceof UnstructuredError && error.code === "FILE_ID_EXPIRED") {
        this.logger.warn?.("Uploaded file ID expired before parsing", { fileId: uploaded.file_id });
      }
      throw error;
    }
  }

  private async parse(fileId: string, output?: "elements"): Promise<ParseResponse> {
    const form = new FormData();
    form.append("file_id", fileId);
    if (output) form.append("output", output);
    const response = await this.request("/parse", { method: "POST", body: form }, "PARSE_FAILED");
    return parseResponse(response);
  }

  private async request(
    path: string,
    init: RequestInit,
    failureCode: "UPLOAD_FAILED" | "PARSE_FAILED",
  ): Promise<unknown> {
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await fetch(`${this.baseUrl}${path}`, {
          ...init,
          headers: { ...(init.headers ?? {}), "unstructured-api-key": this.apiKey },
          signal: controller.signal,
        });
        const raw = await response.text();
        let body: unknown;
        try { body = raw ? JSON.parse(raw) : {}; } catch { body = { message: raw }; }
        if (response.ok) return body;
        if (response.status === 404 && path === "/parse") {
          throw new UnstructuredError("The uploaded file_id is missing or expired", "FILE_ID_EXPIRED", 404);
        }
        if (response.status === 400 || response.status === 415 || response.status === 422) {
          throw new UnstructuredError("Unstructured rejected the PDF or request", "INVALID_PDF", response.status, body);
        }
        if (!this.retryable(response.status) || attempt === this.retries) {
          throw new UnstructuredError(`${failureCode}: HTTP ${response.status}`, failureCode, response.status, body);
        }
        this.logger.warn?.("Retrying Unstructured request", { path, attempt: attempt + 1, status: response.status });
      } catch (error) {
        if (error instanceof UnstructuredError) throw error;
        const timeout = error instanceof DOMException && error.name === "AbortError";
        if (attempt === this.retries) {
          throw new UnstructuredError(
            timeout ? "Unstructured request timed out" : "Unstructured network request failed",
            timeout ? "NETWORK_TIMEOUT" : failureCode,
            undefined,
            error,
          );
        }
        this.logger.warn?.("Retrying Unstructured network request", { path, attempt: attempt + 1 });
      } finally {
        clearTimeout(timer);
      }
      await new Promise((resolve) => setTimeout(resolve, 250 * 2 ** attempt));
    }
    throw new UnstructuredError("Unstructured request failed", failureCode);
  }

  private retryable(status: number): boolean { return status === 408 || status === 429 || status >= 500; }

  private readApiKey(): string {
    const runtime = globalThis as typeof globalThis & { process?: { env?: Record<string, string | undefined> } };
    return runtime.process?.env?.UNSTRUCTURED_API_KEY ?? "";
  }

  private toBlob(input: UploadInput): Blob {
    const data = typeof input === "object" && "data" in input ? input.data : input;
    if (data instanceof Blob) return data;
    if (data instanceof ArrayBuffer) return new Blob([data], { type: "application/pdf" });
    return new Blob([data as unknown as BlobPart], { type: "application/pdf" });
  }

  private filename(input: UploadInput): string {
    return typeof input === "object" && "filename" in input ? input.filename : "document.pdf";
  }
}
