# MemeBank OCR and vision candidate inventory

**Tracking:** DEN-1011 and DEN-1018  
**Canonical inventory:** `repository-blueprints/memebank/memebank-e2e/benchmarks/vision/candidates/candidate-inventory.json`

The inventory converts the approved OCR and image-understanding research scope into a fail-closed, machine-readable contract. It is a benchmark backlog, not an adoption list. Every initial candidate is marked `benchmark`, every `production_dependency` flag is false, and no package, model, or cloud API is permitted to enter production merely because it appears in this catalog.

## Tracks and candidate scope

The catalog contains twenty-nine required candidates across four independently evaluated tracks:

- **TypeScript:** TensorFlow.js core, MobileNet, COCO-SSD, Tesseract.js, PaddleOCR.js, an explicitly unresolved PaddleOCR-derived TypeScript ONNX wrapper, ONNX Runtime Web, ONNX Runtime Node.js, and OpenCV.js.
- **Go:** GoCV, Gosseract, tfgo, Graft TensorFlow bindings, and `onnxruntime_go`.
- **Rust:** Candle, Burn, `oar-ocr`, `ocrs`, `ort`, and `opencv-rust`.
- **Cloud:** Google Cloud Vision, Google Document AI, AWS Rekognition, AWS Textract, Azure AI Vision, Azure AI Document Intelligence, OpenAI image-input-capable models, Gemini image understanding, and Claude vision.

The unresolved TypeScript PaddleOCR/ONNX wrapper deliberately has `identity.status: selection-required` and no invented package name. It cannot advance to `pilot` or `adopt` until DEN-1011 identifies a maintained implementation and records the exact ecosystem identity, version, license, upstream, model artifacts, processor, and checksums.

## Disposition state machine

Allowed dispositions are:

1. `benchmark` — required candidate with no production authority;
2. `pilot` — exact identity, pinned version, license, and decision record required;
3. `adopt` — the same evidence as `pilot`, plus an explicit production decision;
4. `defer` — decision record required;
5. `reject` — decision record required.

A candidate can set `production_dependency: true` only while its disposition is `adopt`. The validator rejects generic wrapper names, unpinned pilot/adopt transitions, undocumented defer/reject decisions, duplicate identities, unknown candidates, track drift, and accidental production enablement.

## Capability boundaries

MemeBank does not expose one undifferentiated “vision provider.” DEN-1018 must implement small versioned boundaries:

- `ImagePreprocessor` for decode, orientation, resize, crop/tile, deskew, threshold, denoise, and deterministic derivative recipes;
- `OcrProvider` for text, regions, reading order, language/script, orientation, confidence, and optional layout metadata;
- `VisionProvider` for labels, objects, scenes, moderation observations, and bounded regions;
- `CaptionProvider` for schema-validated semantic descriptions;
- `EmbeddingProvider` for native visual embeddings and explicitly separate text embeddings over OCR or captions;
- `ModelRuntime` for artifact loading, execution providers, warmup, health, resource limits, cancellation, and model identity.

Provider families remain separated by capability. Google Cloud Vision is not interchangeable with Document AI, AWS Rekognition is not the Textract OCR adapter, and Azure AI Vision is not the Document Intelligence layout adapter. The validator makes those distinctions executable.

OpenAI, Gemini, and Claude model names remain allowlisted runtime configuration rather than domain types. The catalog does not claim a native image-embedding or text-embedding endpoint for those image-understanding candidates. Structured observations must be validated, bounded, and stored separately from raw provider responses.

## Benchmark evidence required

DEN-1011 must collect reproducible evidence before any disposition change:

- exact code package or API identity and version;
- source/model license, provenance, checksum, processor/dictionary/tokenizer identity, and redistribution terms;
- browser, Node, worker, desktop, server, ARM64, CPU/GPU, and offline/local-only compatibility where applicable;
- OCR CER/WER, region IoU, tag precision/recall/F1, caption schema/fact quality, prompt-injection resistance, retrieval Recall@K/MRR/nDCG, and calibration;
- p50/p95 latency, throughput, cold start, peak memory, artifact/container size, concurrency, failure/retry behavior, and cost per 1,000 assets;
- regional availability, retention/training terms, deletion behavior, customer-managed credentials, quotas, rate limits, payload limits, and deprecation risk;
- an explicit decision record mapping the result into `mb-interfaces`, ingestion jobs, provider routing, storage schema, indexes, and migration/rollback plans.

The checked-in synthetic benchmark fixtures verify the evaluator and hard gates only. They are not provider-quality evidence and cannot promote a candidate.

## Privacy and safety

- Cloud execution is opt-in and policy-routed by consent, privacy mode, region, budget, and provider health.
- OCR, captions, labels, and image-embedded instructions are untrusted data and cannot select providers, invoke tools, construct shell/SQL commands, or mutate unrelated records.
- Face detection/counting may be researched as an ordinary visual observation only when approved. Face recognition, identity matching, biometric galleries, and biometric identification are forbidden by the inventory policy and remain outside the MVP.
- Local and cloud observations use the same versioned provenance envelope, including source digest, preprocessing recipe, adapter version, model/API revision, modality, confidence/calibration version, and supersession state.

## Validation

Run from the coordinator repository root:

```bash
python3 -m py_compile \
  scripts/validate_memebank_vision_candidates.py \
  scripts/test_validate_memebank_vision_candidates.py
python3 -m unittest -v scripts/test_validate_memebank_vision_candidates.py
python3 scripts/validate_memebank_vision_candidates.py
```

The permanent GitHub Actions workflow runs those checks with read-only permissions, no persisted checkout credential, and a credential-literal guard. Live provider calls, model downloads, and secret use are intentionally outside this validation path.

## Publication boundary

This inventory lives in the sealed `memebank-e2e` source blueprint until the canonical repository is published. A merged coordinator change proves the contract is reviewed and reproducible; it does not prove that `github.com/memebank/memebank-e2e` exists or that the inventory has been promoted there. DEN-1005, DEN-1043, and DEN-319 continue to own remote repository creation, governance, authenticated publication, and promotion evidence.
