package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
)

const schemaVersion = "ores.code-config-audit.v1"
const maxFiles = 4096
const maxBytes int64 = 64 * 1024 * 1024
const maxFileBytes int64 = 2 * 1024 * 1024

var skipDirs = map[string]bool{
	".git": true, ".dart_tool": true, ".idea": true, ".next": true,
	".turbo": true, ".zed": true, "build": true, "coverage": true,
	"dist": true, "generated": true, "node_modules": true, "target": true,
	"vendor": true, "zed_modules": true,
}

var regexPatterns = []struct {
	kind string
	re   *regexp.Regexp
}{
	{"config-literal", regexp.MustCompile(`["` + "`" + `]([^"` + "`" + `\n]*\.toml)["` + "`" + `]`)},
	{"env-api", regexp.MustCompile(`\b(os\.(?:Getenv|LookupEnv))\b`)},
	{"toml-api", regexp.MustCompile(`\b(toml\.(?:Decode|Unmarshal|NewDecoder))\b`)},
}

type Event struct {
	Kind   string `json:"kind"`
	Value  string `json:"value,omitempty"`
	Line   uint32 `json:"line"`
	Column uint32 `json:"column"`
}

type FileReceipt struct {
	Path        string  `json:"path"`
	SyntaxValid bool    `json:"syntaxValid"`
	RegexHits   []Event `json:"regexHits"`
	ASTEvents   []Event `json:"astEvents"`
}

type Receipt struct {
	SchemaVersion string        `json:"schemaVersion"`
	Language      string        `json:"language"`
	Parser        string        `json:"parser"`
	ParserVersion string        `json:"parserVersion"`
	Files         []FileReceipt `json:"files"`
}

func main() {
	rootFlag := flag.String("root", "", "repository root")
	formatFlag := flag.String("format", "", "output format")
	flag.Parse()
	if *rootFlag == "" || *formatFlag != "json" || flag.NArg() != 0 {
		fatal("usage: ores-ast-go --root <path> --format json")
	}
	root, err := filepath.EvalSymlinks(*rootFlag)
	if err != nil {
		fatal("root could not be resolved")
	}
	root, err = filepath.Abs(root)
	if err != nil {
		fatal("root could not be made absolute")
	}

	files, err := inventory(root)
	if err != nil {
		fatal(err.Error())
	}
	fset := token.NewFileSet()
	receipts := make([]FileReceipt, 0, len(files))
	for _, file := range files {
		receipts = append(receipts, inspectFile(root, file, fset))
	}
	sort.Slice(receipts, func(i, j int) bool { return receipts[i].Path < receipts[j].Path })

	encoded, err := json.Marshal(Receipt{
		SchemaVersion: schemaVersion,
		Language:      "go",
		Parser:        "go/parser+go/ast",
		ParserVersion: runtime.Version(),
		Files:         receipts,
	})
	if err != nil {
		fatal("receipt serialization failed")
	}
	fmt.Println(string(encoded))
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, message)
	os.Exit(2)
}

func inventory(root string) ([]string, error) {
	files := make([]string, 0)
	var total int64
	err := filepath.WalkDir(root, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if entry.IsDir() {
			if filePath != root && skipDirs[entry.Name()] {
				return filepath.SkipDir
			}
			return nil
		}
		if filepath.Ext(entry.Name()) != ".go" {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Size() > maxFileBytes {
			return fmt.Errorf("source file exceeds bound")
		}
		total += info.Size()
		if total > maxBytes || len(files) >= maxFiles {
			return fmt.Errorf("source inventory exceeds bound")
		}
		files = append(files, filePath)
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(files)
	return files, nil
}

func inspectFile(root, file string, fset *token.FileSet) FileReceipt {
	textBytes, err := os.ReadFile(file)
	if err != nil {
		fatal("source file could not be read")
	}
	text := string(textBytes)
	parsed, parseErr := parser.ParseFile(fset, file, text, parser.AllErrors)
	result := FileReceipt{
		Path:        relative(root, file),
		SyntaxValid: parseErr == nil,
		RegexHits:   regexLane(text),
		ASTEvents:   []Event{},
	}
	if parsed != nil {
		result.ASTEvents = astLane(parsed, fset)
	}
	return result
}

func regexLane(text string) []Event {
	events := make([]Event, 0)
	lineStarts := []int{0}
	for i, r := range text {
		if r == '\n' {
			lineStarts = append(lineStarts, i+1)
		}
	}
	for _, pattern := range regexPatterns {
		for _, match := range pattern.re.FindAllStringSubmatchIndex(text, -1) {
			start := match[0]
			value := text[match[2]:match[3]]
			line, column := positionFromOffset(lineStarts, start)
			events = append(events, Event{Kind: pattern.kind, Value: value, Line: line, Column: column})
		}
	}
	return dedupe(events)
}

func importAliases(file *ast.File) map[string]string {
	aliases := make(map[string]string)
	for _, spec := range file.Imports {
		importPath, err := strconv.Unquote(spec.Path.Value)
		if err != nil || importPath == "" {
			continue
		}
		name := path.Base(importPath)
		if spec.Name != nil {
			name = spec.Name.Name
		}
		if name == "_" || name == "." || name == "" {
			continue
		}
		aliases[name] = importPath
	}
	return aliases
}

func isTomlImport(importPath string) bool {
	return importPath == "github.com/BurntSushi/toml" ||
		strings.Contains(importPath, "pelletier/go-toml") ||
		strings.HasSuffix(importPath, "/toml")
}

func astLane(file *ast.File, fset *token.FileSet) []Event {
	events := make([]Event, 0)
	aliases := importAliases(file)
	ast.Inspect(file, func(node ast.Node) bool {
		call, ok := node.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		pkg, ok := sel.X.(*ast.Ident)
		if !ok {
			return true
		}
		pos := fset.Position(call.Pos())
		pkgPath := aliases[pkg.Name]
		isOS := pkg.Name == "os" || pkgPath == "os"
		isToml := pkg.Name == "toml" || isTomlImport(pkgPath)
		if isOS && (sel.Sel.Name == "Getenv" || sel.Sel.Name == "LookupEnv") {
			events = append(events, Event{
				Kind: "env-read", Value: firstString(call.Args),
				Line: uint32(pos.Line), Column: uint32(pos.Column),
			})
		}
		if isOS && (sel.Sel.Name == "ReadFile" || sel.Sel.Name == "Open") {
			if value := firstString(call.Args); strings.HasSuffix(value, ".toml") {
				events = append(events, Event{
					Kind: "config-read", Value: value,
					Line: uint32(pos.Line), Column: uint32(pos.Column),
				})
			}
		}
		if isToml && (sel.Sel.Name == "Decode" || sel.Sel.Name == "Unmarshal" || sel.Sel.Name == "NewDecoder") {
			events = append(events, Event{
				Kind: "toml-parse", Line: uint32(pos.Line), Column: uint32(pos.Column),
			})
		}
		return true
	})
	return dedupe(events)
}

func firstString(args []ast.Expr) string {
	if len(args) == 0 {
		return ""
	}
	literal, ok := args[0].(*ast.BasicLit)
	if !ok || literal.Kind != token.STRING {
		return ""
	}
	value, err := strconv.Unquote(literal.Value)
	if err != nil {
		return ""
	}
	return value
}

func relative(root, file string) string {
	rel, err := filepath.Rel(root, file)
	if err != nil || rel == "." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		fatal("source escaped root")
	}
	return filepath.ToSlash(rel)
}

func positionFromOffset(lineStarts []int, offset int) (uint32, uint32) {
	lineIndex := sort.Search(len(lineStarts), func(i int) bool { return lineStarts[i] > offset }) - 1
	if lineIndex < 0 {
		lineIndex = 0
	}
	return uint32(lineIndex + 1), uint32(offset - lineStarts[lineIndex] + 1)
}

func dedupe(events []Event) []Event {
	seen := make(map[string]bool)
	result := make([]Event, 0, len(events))
	for _, event := range events {
		key := fmt.Sprintf("%s\x00%s\x00%d\x00%d", event.Kind, event.Value, event.Line, event.Column)
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, event)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Line != result[j].Line {
			return result[i].Line < result[j].Line
		}
		if result[i].Column != result[j].Column {
			return result[i].Column < result[j].Column
		}
		return result[i].Kind < result[j].Kind
	})
	return result
}
