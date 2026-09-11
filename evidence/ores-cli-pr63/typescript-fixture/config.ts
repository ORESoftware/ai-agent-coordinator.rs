import { readFileSync } from 'node:fs';
import * as TOML from '@iarna/toml';

// Regex must see this text, but AST must not report executable I/O: ".comment-only.toml".
const config = readFileSync('.ores-rl.toml', 'utf8');
const parsed = TOML.parse(config);
const redisUrl = process.env.REDIS_URL;
const { API_KEY, DATABASE_URL: databaseUrl, ...rest } = process.env;
void parsed;
void redisUrl;
void API_KEY;
void databaseUrl;
void rest;