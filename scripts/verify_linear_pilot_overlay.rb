#!/usr/bin/env ruby
# frozen_string_literal: true

require 'yaml'

ROOT = 'deploy/k8s'
OVERLAY = 'deploy/overlays/cross-org-linear-pilot'
EXPECTED_RUNTIME_SHA = ENV.fetch('VERIFIED_RUNTIME_IMAGE_SHA')
EXPECTED_IMAGE = "ghcr.io/oresoftware/ai-agent-coordinator:sha-#{EXPECTED_RUNTIME_SHA}"


def abort_contract(message)
  warn("linear pilot deployment contract: #{message}")
  exit(1)
end


def load_one(path)
  YAML.safe_load(File.read(path), aliases: false)
rescue StandardError => error
  abort_contract("cannot parse #{path}: #{error.message}")
end


def mapping(value)
  value.to_s.split(',').map(&:strip).reject(&:empty?).to_h do |entry|
    key, mapped = entry.split('=', 2)
    abort_contract("invalid mapping entry #{entry.inspect}") if key.to_s.empty? || mapped.to_s.empty?
    [key, mapped]
  end
end


def validate_base
  deployment = load_one(File.join(ROOT, 'deployment.yaml'))
  image = deployment.dig('spec', 'template', 'spec', 'containers', 0, 'image')
  abort_contract("base image must be #{EXPECTED_IMAGE}, got #{image.inspect}") unless image == EXPECTED_IMAGE

  env = deployment.dig('spec', 'template', 'spec', 'containers', 0, 'env').to_h do |entry|
    [entry.fetch('name'), entry['value']]
  end
  abort_contract('base repository administration must remain disabled') unless env['GITHUB_REPOSITORY_ADMIN_ENABLED'] == 'false'
  abort_contract('base must not enable cross-org push intake') if env.key?('GITHUB_AUTO_ENQUEUE_PUSHES')
  abort_contract('base must not enable Linear delivery') if env.key?('LINEAR_DELIVERY_ENABLED')
end


def validate_overlay_source
  kustomization = load_one(File.join(OVERLAY, 'kustomization.yaml'))
  expected_resources = ['../../k8s', 'external-secret.yaml']
  abort_contract('overlay must import the complete sibling base') unless kustomization.fetch('resources') == expected_resources

  patch_paths = kustomization.fetch('patches').map do |patch|
    patch.is_a?(Hash) ? patch.fetch('path') : patch
  end
  expected_patches = ['telemetry-config-patch.yaml', 'deployment-patch.yaml']
  abort_contract('unexpected pilot patch set') unless patch_paths == expected_patches

  external_secret = load_one(File.join(OVERLAY, 'external-secret.yaml'))
  abort_contract('pilot credentials must use ExternalSecret') unless external_secret['kind'] == 'ExternalSecret'
  abort_contract('pilot ExternalSecret namespace mismatch') unless external_secret.dig('metadata', 'namespace') == 'ai-agent-coordinator'
  abort_contract('pilot ExternalSecret name mismatch') unless external_secret.dig('metadata', 'name') == 'ai-agent-coordinator-linear-pilot'
  expected_store = {'kind' => 'ClusterSecretStore', 'name' => 'dd-cluster-secrets'}
  abort_contract('pilot must use the protected cluster secret store') unless external_secret.dig('spec', 'secretStoreRef') == expected_store
  abort_contract('pilot target secret mismatch') unless external_secret.dig('spec', 'target', 'name') == 'ai-agent-coordinator-linear-pilot'

  secret_data = external_secret.dig('spec', 'data')
  expected_secret_keys = %w[
    GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB
    GITHUB_WEBHOOK_SECRET_SONUS_AURIS
    LINEAR_API_TOKEN
  ]
  actual_secret_keys = secret_data.map { |entry| entry.fetch('secretKey') }.sort
  abort_contract('pilot bundle must contain exactly the three protected references') unless actual_secret_keys == expected_secret_keys
  secret_data.each do |entry|
    remote = entry.fetch('remoteRef')
    abort_contract('pilot secret must use the dedicated remote bundle') unless remote['key'] == 'dd/remote-dev/ai-agent-coordinator-linear-pilot'
    abort_contract('remote property must match target key') unless remote['property'] == entry['secretKey']
  end

  config = load_one(File.join(OVERLAY, 'telemetry-config-patch.yaml')).fetch('data')
  expected_org_secrets = {
    'sonus-auris' => 'GITHUB_WEBHOOK_SECRET_SONUS_AURIS',
    'daedalus-fab' => 'GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB'
  }
  abort_contract('organization secret mapping is not exact') unless mapping(config.fetch('GITHUB_WEBHOOK_ORG_SECRET_ENVS')) == expected_org_secrets

  allowed = config.fetch('GITHUB_PUSH_ALLOWED_REPOSITORIES').split(',').map(&:strip)
  expected_allowed = [
    'sonus-auris/sonus-auris-site.web',
    'daedalus-fab/daedalus-clients'
  ]
  abort_contract('pilot repository allowlist is not exact') unless allowed == expected_allowed

  expected_branches = {
    'sonus-auris/sonus-auris-site.web' => 'main',
    'daedalus-fab/daedalus-clients' => 'main'
  }
  abort_contract('pilot default-branch map is not exact') unless mapping(config.fetch('GITHUB_PUSH_DEFAULT_BRANCHES')) == expected_branches
  abort_contract('push intake must be enabled only in the overlay') unless config['GITHUB_AUTO_ENQUEUE_PUSHES'] == 'true'
  abort_contract('Linear delivery must be enabled only in the overlay') unless config['LINEAR_DELIVERY_ENABLED'] == 'true'
  abort_contract('pilot must remain dry-run') unless config['LINEAR_DELIVERY_DRY_RUN'] == 'true'
  abort_contract('pilot must use the official HTTPS Linear endpoint') unless config['LINEAR_API_URL'] == 'https://api.linear.app/graphql'
  abort_contract('pilot auth scheme must remain api_key') unless config['LINEAR_API_AUTH_SCHEME'] == 'api_key'
  if config.key?('LINEAR_COMPLETED_STATE_IDS') || config.key?('LINEAR_COMPLETED_STATE_ID')
    abort_contract('completed-state IDs are forbidden in the dry-run overlay')
  end

  base_config = load_one(File.join(ROOT, 'telemetry-config.yaml')).fetch('data')
  projects = mapping(base_config.fetch('LINEAR_PROJECT_NAMES'))
  abort_contract('Sonus Auris project mapping missing') unless projects['sonus-auris'] == 'github.com/sonus-auris'
  abort_contract('Daedalus Fab project mapping missing') unless projects['daedalus-fab'] == 'github.com/daedalus-fab'
  abort_contract('Linear team must remain DEN') unless base_config['LINEAR_TEAM_KEY'] == 'DEN'

  deployment_patch = load_one(File.join(OVERLAY, 'deployment-patch.yaml'))
  annotation = deployment_patch.dig('spec', 'template', 'metadata', 'annotations', 'oresoftware.dev/cross-org-linear-pilot')
  abort_contract('pilot Deployment must carry a visible dry-run annotation') unless annotation == 'dry-run'

  env_from = deployment_patch.dig('spec', 'template', 'spec', 'containers', 0, 'envFrom')
  refs = env_from.map do |entry|
    entry.dig('secretRef', 'name') || entry.dig('configMapRef', 'name')
  end
  expected_refs = %w[
    ai-agent-coordinator-telemetry
    ai-agent-coordinator-core
    ai-agent-coordinator-telemetry
    ai-agent-coordinator-linear-pilot
    ai-agent-coordinator-admin
    ai-agent-coordinator-providers
  ]
  abort_contract('pilot environment sources are incomplete or reordered') unless refs == expected_refs
  pilot_ref = env_from.find { |entry| entry.dig('secretRef', 'name') == 'ai-agent-coordinator-linear-pilot' }
  abort_contract('pilot secret reference must be required') if pilot_ref.dig('secretRef', 'optional') == true

  overlay_text = Dir[File.join(OVERLAY, '*.yaml')].map { |path| File.read(path) }.join("\n")
  abort_contract('plaintext Secret objects are forbidden') if overlay_text.match?(/^kind:\s*Secret\s*$/)
  abort_contract('GitHub token-like values are forbidden') if overlay_text.match?(/gh[pousr]_[A-Za-z0-9_]{20,}/)
  abort_contract('OpenAI-style key values are forbidden') if overlay_text.match?(/sk-[A-Za-z0-9_-]{16,}/)
end


def validate_rendered(path)
  resources = YAML.load_stream(File.read(path)).compact
  abort_contract('rendered overlay contains a plaintext Secret') if resources.any? { |resource| resource['kind'] == 'Secret' }

  deployment = resources.find do |resource|
    resource['kind'] == 'Deployment' && resource.dig('metadata', 'name') == 'ai-agent-coordinator'
  end
  abort_contract('rendered Deployment missing') unless deployment

  config = resources.find do |resource|
    resource['kind'] == 'ConfigMap' && resource.dig('metadata', 'name') == 'ai-agent-coordinator-telemetry'
  end
  abort_contract('rendered telemetry ConfigMap missing') unless config

  external = resources.find do |resource|
    resource['kind'] == 'ExternalSecret' && resource.dig('metadata', 'name') == 'ai-agent-coordinator-linear-pilot'
  end
  abort_contract('rendered pilot ExternalSecret missing') unless external

  refs = deployment.dig('spec', 'template', 'spec', 'containers', 0, 'envFrom')
  abort_contract('rendered Deployment does not consume pilot secret') unless refs.any? { |entry| entry.dig('secretRef', 'name') == 'ai-agent-coordinator-linear-pilot' }
  abort_contract('rendered pilot is not dry-run') unless config.dig('data', 'LINEAR_DELIVERY_DRY_RUN') == 'true'
  abort_contract('rendered pilot contains completed-state IDs') if config.fetch('data').key?('LINEAR_COMPLETED_STATE_IDS')
  abort_contract('rendered pilot secret target mismatch') unless external.dig('spec', 'target', 'name') == 'ai-agent-coordinator-linear-pilot'
end

validate_base
validate_overlay_source
validate_rendered(ARGV.fetch(0)) if ARGV.any?
puts('Linear pilot overlay contract is valid.')
