package fixture

import operatingSystem "os"

// Regex must see this text, but AST must not report executable I/O: ".comment-only.toml".
func load() {
	_, _ = operatingSystem.LookupEnv("REDIS_URL")
	_, _ = operatingSystem.ReadFile(".ores-lru.toml")
}
