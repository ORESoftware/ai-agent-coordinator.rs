namespace Ores.Cli.SourceAudit.V1;

enum SourceLanguage {
  rust,
  typescript,
  go,
  dart,
  gleam,
}

enum RegexEventKind {
  configLiteral: "config-literal",
  envApi: "env-api",
  tomlApi: "toml-api",
}

enum AstEventKind {
  configRead: "config-read",
  envRead: "env-read",
  tomlParse: "toml-parse",
}

model RegexEvent {
  kind: RegexEventKind;
  @maxLength(512)
  value?: string;
  line: uint32;
  column: uint32;
}

model AstEvent {
  kind: AstEventKind;
  @maxLength(512)
  value?: string;
  line: uint32;
  column: uint32;
}

model SourceFileEvidence {
  @minLength(1)
  @maxLength(1024)
  path: string;
  syntaxValid: boolean;
  @maxItems(4096)
  regexHits: RegexEvent[];
  @maxItems(4096)
  astEvents: AstEvent[];
}

model CodeConfigAuditReceipt {
  schemaVersion: "ores.code-config-audit.v1";
  language: SourceLanguage;
  @minLength(1)
  @maxLength(128)
  parser: string;
  @minLength(1)
  @maxLength(128)
  parserVersion: string;
  @maxItems(4096)
  files: SourceFileEvidence[];
}
