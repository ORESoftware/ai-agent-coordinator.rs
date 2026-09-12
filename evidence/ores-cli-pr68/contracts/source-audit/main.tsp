namespace Ores.Cli.SourceAudit.V1;

enum SourceLanguage {
  rust,
  typescript,
  go,
  dart,
  gleam,
}

enum SourceParser {
  syn,
  tsMorph: "ts-morph",
  goAst: "go/parser+go/ast",
  dartAnalyzer: "package:analyzer",
  treeSitterGleam: "tree-sitter-gleam",
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
  @minValue(1)
  line: uint32;
  @minValue(1)
  column: uint32;
}

model AstEvent {
  kind: AstEventKind;
  @maxLength(512)
  value?: string;
  @minValue(1)
  line: uint32;
  @minValue(1)
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
  parser: SourceParser;
  @minLength(1)
  @maxLength(128)
  parserVersion: string;
  @maxItems(4096)
  files: SourceFileEvidence[];
}
