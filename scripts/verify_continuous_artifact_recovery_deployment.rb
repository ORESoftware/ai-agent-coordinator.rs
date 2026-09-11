#!/usr/bin/env ruby
# frozen_string_literal: true

require 'yaml'

ROOT = 'deploy/continuous-artifact-recovery/k8s'
EXPECTED_IMAGE = 'ghcr.io/oresoftware/ai-agent-coordinator-artifact-recovery:sha-5af90a5f89b76a91c348f2e6d5e52cf06e3fc311'
EXPECTED_REMOTE_KEY = 'dd/remote-dev/ai-agent-coordinator-artifact-recovery'
EXPECTED_SECRET = 'ai-agent-coordinator-artifact-recovery'
EXPECTED_CONFIG = {
  'ARTIFACT_RECOVERY_WORKER_COUNT' => '3',
  'ARTIFACT_RECOVERY_WINDOW_HOURS' => '1200',
  'ARTIFACT_RECOVERY_OVERLAP_HOURS' => '6',
  'ARTIFACT_RECOVERY_LEASE_SECONDS' => '300',
  'ARTIFACT_RECOVERY_POLL_SECONDS' => '30'
}.freeze
REQUIRED_SECRET_KEYS = %w[
  AI_AGENT_COORDINATOR_URL
  AI_AGENT_COORDINATOR_API_TOKEN
  CHATGPT_RECOVERY_SOURCE_TOKEN
  GITHUB_RECOVERY_SOURCE_TOKEN
  LINEAR_RECOVERY_SOURCE_TOKEN
  LOCAL_REPO_RECOVERY_SOURCE_TOKEN
  FILE_LIBRARY_RECOVERY_SOURCE_TOKEN
].freeze

def abort_contract(message)
  warn("continuous artifact-recovery deployment contract: #{message}")
  exit(1)
end

def load_one(path)
  YAML.safe_load(File.read(path), aliases: false)
rescue StandardError => error
  abort_contract("cannot parse #{path}: #{error.message}")
end

def resources_from(path)
  YAML.load_stream(File.read(path)).compact
rescue StandardError => error
  abort_contract("cannot parse rendered manifest #{path}: #{error.message}")
end

def validate_resources(resources)
  abort_contract('plaintext Secret objects are forbidden') if resources.any? { |resource| resource['kind'] == 'Secret' }
  resources.each do |resource|
    abort_contract("#{resource['kind']} namespace mismatch") unless resource.dig('metadata', 'namespace') == 'ai-agent-coordinator'
  end

  by_kind = resources.group_by { |resource| resource['kind'] }
  expected_counts = {
    'ConfigMap' => 1,
    'PersistentVolumeClaim' => 1,
    'ExternalSecret' => 1,
    'NetworkPolicy' => 1,
    'Deployment' => 1
  }
  abort_contract("unexpected resource kinds #{by_kind.keys.sort.inspect}") unless by_kind.keys.sort == expected_counts.keys.sort
  expected_counts.each do |kind, count|
    abort_contract("expected #{count} #{kind}, got #{by_kind.fetch(kind, []).length}") unless by_kind.fetch(kind, []).length == count
  end

  config = by_kind.fetch('ConfigMap').first
  abort_contract('ConfigMap name mismatch') unless config.dig('metadata', 'name') == EXPECTED_SECRET
  EXPECTED_CONFIG.each do |key, value|
    abort_contract("#{key} must be #{value}") unless config.dig('data', key) == value
  end
  abort_contract('worker count above three is forbidden') unless config.dig('data', 'ARTIFACT_RECOVERY_WORKER_COUNT').to_i <= 3

  external = by_kind.fetch('ExternalSecret').first
  abort_contract('ExternalSecret name mismatch') unless external.dig('metadata', 'name') == EXPECTED_SECRET
  abort_contract('ExternalSecret target mismatch') unless external.dig('spec', 'target', 'name') == EXPECTED_SECRET
  expected_store = {'kind' => 'ClusterSecretStore', 'name' => 'dd-cluster-secrets'}
  abort_contract('ExternalSecret store mismatch') unless external.dig('spec', 'secretStoreRef') == expected_store
  abort_contract('ExternalSecret must retain the projected Secret on deletion') unless external.dig('spec', 'target', 'deletionPolicy') == 'Retain'
  extract_key = external.dig('spec', 'dataFrom', 0, 'extract', 'key')
  abort_contract('ExternalSecret must use the dedicated protected bundle') unless extract_key == EXPECTED_REMOTE_KEY

  deployment = by_kind.fetch('Deployment').first
  abort_contract('Deployment name mismatch') unless deployment.dig('metadata', 'name') == EXPECTED_SECRET
  abort_contract('Deployment must remain replicas zero before activation') unless deployment.dig('spec', 'replicas') == 0
  abort_contract('Deployment must visibly remain disabled') unless deployment.dig('metadata', 'annotations', 'oresoftware.dev/activation') == 'disabled'
  abort_contract('pod template must visibly remain disabled') unless deployment.dig('spec', 'template', 'metadata', 'annotations', 'oresoftware.dev/activation') == 'disabled'
  abort_contract('RWO workload must use Recreate') unless deployment.dig('spec', 'strategy', 'type') == 'Recreate'

  pod = deployment.dig('spec', 'template', 'spec')
  abort_contract('service account token must not be mounted') unless pod['automountServiceAccountToken'] == false
  abort_contract('wrong service account') unless pod['serviceAccountName'] == 'ai-agent-coordinator'
  abort_contract('pod must run as non-root UID/GID 10001') unless pod.dig('securityContext', 'runAsNonRoot') == true &&
    pod.dig('securityContext', 'runAsUser') == 10_001 &&
    pod.dig('securityContext', 'runAsGroup') == 10_001
  abort_contract('pod must use RuntimeDefault seccomp') unless pod.dig('securityContext', 'seccompProfile', 'type') == 'RuntimeDefault'
  abort_contract('Deployment must contain one worker container') unless pod.fetch('containers').length == 1

  container = pod.fetch('containers').first
  abort_contract('worker image is not the reviewed immutable image') unless container['image'] == EXPECTED_IMAGE
  security = container.fetch('securityContext')
  abort_contract('privilege escalation must be disabled') unless security['allowPrivilegeEscalation'] == false
  abort_contract('root filesystem must be read-only') unless security['readOnlyRootFilesystem'] == true
  abort_contract('all capabilities must be dropped') unless security.dig('capabilities', 'drop') == ['ALL']
  config_refs = container.fetch('envFrom').map { |entry| entry.dig('configMapRef', 'name') }
  abort_contract('worker must consume only the dedicated ConfigMap through envFrom') unless config_refs == [EXPECTED_SECRET]

  env = container.fetch('env').to_h { |entry| [entry.fetch('name'), entry] }
  REQUIRED_SECRET_KEYS.each do |key|
    ref = env.dig(key, 'valueFrom', 'secretKeyRef')
    abort_contract("#{key} must come from the dedicated projected Secret") unless ref &&
      ref['name'] == EXPECTED_SECRET &&
      ref['key'] == key &&
      ref['optional'] != true
  end
  claude = env.dig('CLAUDE_RECOVERY_SOURCE_TOKEN', 'valueFrom', 'secretKeyRef')
  abort_contract('Claude token must be optional and use the dedicated Secret') unless claude == {
    'name' => EXPECTED_SECRET,
    'key' => 'CLAUDE_RECOVERY_SOURCE_TOKEN',
    'optional' => true
  }

  mounts = container.fetch('volumeMounts').to_h { |mount| [mount.fetch('name'), mount] }
  abort_contract('state mount mismatch') unless mounts.dig('state', 'mountPath') == '/var/lib/artifact-recovery'
  abort_contract('source manifest must be mounted read-only') unless mounts.dig('source-manifest', 'mountPath') == '/etc/artifact-recovery' &&
    mounts.dig('source-manifest', 'readOnly') == true
  abort_contract('temporary path must be explicit') unless mounts.dig('tmp', 'mountPath') == '/tmp'

  volumes = pod.fetch('volumes').to_h { |volume| [volume.fetch('name'), volume] }
  abort_contract('state PVC mismatch') unless volumes.dig('state', 'persistentVolumeClaim', 'claimName') == 'ai-agent-coordinator-artifact-recovery-state'
  abort_contract('manifest secret mismatch') unless volumes.dig('source-manifest', 'secret', 'secretName') == EXPECTED_SECRET
  manifest_items = volumes.dig('source-manifest', 'secret', 'items')
  abort_contract('manifest must map only sources.json') unless manifest_items == [{'key' => 'sources.json', 'path' => 'sources.json'}]
  abort_contract('temporary volume must be bounded memory') unless volumes.dig('tmp', 'emptyDir') == {'medium' => 'Memory', 'sizeLimit' => '64Mi'}

  pvc = by_kind.fetch('PersistentVolumeClaim').first
  abort_contract('PVC name mismatch') unless pvc.dig('metadata', 'name') == 'ai-agent-coordinator-artifact-recovery-state'
  abort_contract('PVC must remain ReadWriteOnce') unless pvc.dig('spec', 'accessModes') == ['ReadWriteOnce']
  abort_contract('PVC storage request must be 5Gi') unless pvc.dig('spec', 'resources', 'requests', 'storage') == '5Gi'

  policy = by_kind.fetch('NetworkPolicy').first
  abort_contract('NetworkPolicy name mismatch') unless policy.dig('metadata', 'name') == EXPECTED_SECRET
  abort_contract('worker must expose no ingress policy') unless policy.dig('spec', 'policyTypes') == ['Egress'] && !policy.dig('spec').key?('ingress')
  egress = policy.dig('spec', 'egress')
  abort_contract('worker egress must have exactly DNS, coordinator, and HTTPS rules') unless egress.is_a?(Array) && egress.length == 3
  ports = egress.flat_map { |rule| rule.fetch('ports', []) }.map { |port| [port['protocol'], port['port']] }
  abort_contract('DNS UDP/TCP, coordinator 8080, and HTTPS 443 egress are required') unless ports.sort == [
    ['TCP', 53], ['UDP', 53], ['TCP', 443], ['TCP', 8080]
  ].sort
  internet = egress.find { |rule| rule.dig('to', 0, 'ipBlock', 'cidr') == '0.0.0.0/0' }
  private_ranges = %w[10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16]
  abort_contract('public HTTPS rule must exclude private, loopback, and metadata ranges') unless internet &&
    internet.dig('to', 0, 'ipBlock', 'except').sort == private_ranges.sort
end

def validate_source
  kustomization = load_one(File.join(ROOT, 'kustomization.yaml'))
  expected = %w[
    config-map.yaml
    persistent-volume-claim.yaml
    external-secret.yaml
    network-policy.yaml
    deployment.yaml
  ]
  abort_contract('kustomization resource order or membership is unexpected') unless kustomization.fetch('resources') == expected
  resources = expected.map { |name| load_one(File.join(ROOT, name)) }
  validate_resources(resources)

  text = Dir[File.join(ROOT, '*')].select { |path| File.file?(path) }.map { |path| File.read(path) }.join("\n")
  abort_contract('conflict markers are forbidden') if text.match?(/^(<<<<<<<|=======|>>>>>>>)/)
  abort_contract('credential-shaped values are forbidden') if text.match?(/(?:ghp_|github_pat_|lin_api_|sk-)[A-Za-z0-9_-]{16,}/)
  private_key_marker = ['-----BEGIN ', 'PRIVATE KEY-----'].join
  abort_contract('private keys are forbidden') if text.include?(private_key_marker)
end

validate_source
validate_resources(resources_from(ARGV.fetch(0))) if ARGV.any?
puts('Continuous artifact-recovery deployment contract is valid.')
