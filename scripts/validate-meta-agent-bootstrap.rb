#!/usr/bin/env ruby
# frozen_string_literal: true

require 'yaml'

MAIN_KUSTOMIZATION = 'deploy/k8s/kustomization.yaml'
BOOTSTRAP_ROOT = 'deploy/k8s/bootstrap/meta-agent'
BOOTSTRAP_KUSTOMIZATION = File.join(BOOTSTRAP_ROOT, 'kustomization.yaml')
BOOTSTRAP_JOB = File.join(BOOTSTRAP_ROOT, 'job.yaml')
EXPECTED_IMAGE = 'curlimages/curl:8.17.0@sha256:935d9100e9ba842cdb060de42472c7ca90cfe9a7c96e4dacb55e79e560b3ff40'
EXPECTED_TARGET = 'meta-agents-demo/meta-agent-control-plane.rs'
EXPECTED_JOB = 'meta-agent-control-plane-repository-bootstrap-20260731'

class ContractError < StandardError; end

def assert!(condition, message)
  raise ContractError, message unless condition
end

def load_yaml(path)
  YAML.safe_load(File.read(path), aliases: false)
end

def deep_copy(value)
  Marshal.load(Marshal.dump(value))
end

def validate_contract!(main_kustomization:, bootstrap_kustomization:, job:)
  main_resources = main_kustomization.fetch('resources')
  assert!(main_resources.is_a?(Array), 'steady-state resources must be an array')
  assert!(main_resources.none? { |resource| resource.include?('bootstrap') || resource.include?('repository') },
          'steady-state deployment must not include repository bootstrap resources')

  bootstrap_resources = bootstrap_kustomization.fetch('resources')
  assert!(bootstrap_resources == ['job.yaml'], 'bootstrap overlay must contain exactly job.yaml')

  assert!(job['apiVersion'] == 'batch/v1', 'bootstrap must use batch/v1')
  assert!(job['kind'] == 'Job', 'bootstrap resource must be a Job')
  metadata = job.fetch('metadata')
  assert!(metadata['name'] == EXPECTED_JOB, 'unexpected bootstrap Job name')
  assert!(metadata['namespace'] == 'ai-agent-coordinator', 'bootstrap namespace drifted')
  assert!(metadata.dig('labels', 'app.kubernetes.io/managed-by') == 'manual-bootstrap',
          'bootstrap must be explicitly marked manual')

  spec = job.fetch('spec')
  assert!(spec['backoffLimit'] == 0, 'bootstrap must never retry')
  assert!(spec['activeDeadlineSeconds'] == 300, 'bootstrap must retain a five-minute deadline')
  assert!(spec['ttlSecondsAfterFinished'] == 86_400, 'bootstrap must self-clean after one day')

  pod = spec.dig('template', 'spec')
  assert!(pod.is_a?(Hash), 'bootstrap pod spec is missing')
  assert!(pod['automountServiceAccountToken'] == false, 'service-account token mounting is forbidden')
  assert!(pod['enableServiceLinks'] == false, 'service-link environment injection is forbidden')
  assert!(pod['hostNetwork'] == false, 'host networking is forbidden')
  assert!(pod['hostPID'] == false, 'host PID namespace is forbidden')
  assert!(pod['hostIPC'] == false, 'host IPC namespace is forbidden')
  assert!(pod['shareProcessNamespace'] == false, 'shared process namespace is forbidden')
  assert!(pod['restartPolicy'] == 'Never', 'bootstrap pod must never restart')
  assert!(pod['terminationGracePeriodSeconds'] == 10, 'bootstrap termination grace period drifted')
  assert!(!pod.key?('initContainers'), 'bootstrap must not have init containers')
  assert!(!pod.key?('ephemeralContainers'), 'bootstrap must not have ephemeral containers')

  pod_security = pod.fetch('securityContext')
  assert!(pod_security['runAsNonRoot'] == true, 'bootstrap must run as non-root')
  assert!(pod_security['runAsUser'] == 10_001 && pod_security['runAsGroup'] == 10_001,
          'bootstrap UID/GID must remain 10001')
  assert!(pod_security.dig('seccompProfile', 'type') == 'RuntimeDefault',
          'bootstrap must use RuntimeDefault seccomp')

  containers = pod.fetch('containers')
  assert!(containers.length == 1, 'bootstrap must have exactly one container')
  container = containers.first
  assert!(container['name'] == 'create-repository', 'unexpected bootstrap container')
  assert!(container['image'] == EXPECTED_IMAGE, 'bootstrap image must remain digest-pinned')
  assert!(container['image'].include?('@sha256:'), 'mutable bootstrap images are forbidden')
  assert!(container['imagePullPolicy'] == 'IfNotPresent', 'bootstrap image pull policy drifted')
  assert!(container['terminationMessagePolicy'] == 'FallbackToLogsOnError',
          'bootstrap termination message policy drifted')

  security = container.fetch('securityContext')
  assert!(security['allowPrivilegeEscalation'] == false, 'privilege escalation must be disabled')
  assert!(security['readOnlyRootFilesystem'] == true, 'root filesystem must be read-only')
  assert!(security.dig('capabilities', 'drop') == ['ALL'], 'all Linux capabilities must be dropped')

  env = container.fetch('env')
  assert!(env.length == 1, 'bootstrap may mount exactly one environment variable')
  token = env.first
  assert!(token['name'] == 'GITHUB_REPOSITORY_ADMIN_TOKEN', 'unexpected credential variable')
  assert!(!token.key?('value'), 'plaintext credential values are forbidden')
  assert!(token.dig('valueFrom', 'secretKeyRef') == {
            'name' => 'ai-agent-coordinator-admin',
            'key' => 'GITHUB_REPOSITORY_ADMIN_TOKEN'
          }, 'bootstrap credential must come from the dedicated admin Secret')

  assert!(container['command'] == ['/bin/sh', '-ec'], 'bootstrap must use a fail-closed shell')
  script = container.fetch('args').join("\n")
  required_fragments = [
    "target='#{EXPECTED_TARGET}'",
    "api='https://api.github.com'",
    '--proto =https',
    '--tlsv1.2',
    '--connect-timeout 10',
    '--max-time 30',
    '/user/memberships/orgs/meta-agents-demo',
    '"login"[[:space:]]*:[[:space:]]*"ORESoftware"',
    '"role"[[:space:]]*:[[:space:]]*"admin"',
    '"state"[[:space:]]*:[[:space:]]*"active"',
    '"visibility":"public"',
    '"auto_init":true',
    'status=failed stage=create',
    'status=failed stage=verify'
  ]
  missing = required_fragments.reject { |fragment| script.include?(fragment) }
  assert!(missing.empty?, "bootstrap script is missing guards: #{missing.inspect}")
  assert!(!script.include?('set +e'), 'bootstrap must not disable fail-fast behavior')
  assert!(!script.match?(/Authorization:\s*Bearer\s+[^$]/), 'literal authorization material is forbidden')

  assert!(container.fetch('volumeMounts') == [{ 'name' => 'tmp', 'mountPath' => '/tmp' }],
          'bootstrap may mount only the in-memory temporary volume')
  assert!(pod.fetch('volumes') == [{
            'name' => 'tmp',
            'emptyDir' => { 'medium' => 'Memory', 'sizeLimit' => '8Mi' }
          }], 'bootstrap temporary storage must remain a bounded 8Mi memory volume')

  resources = container.fetch('resources')
  assert!(resources.dig('requests', 'cpu') == '5m', 'bootstrap CPU request drifted')
  assert!(resources.dig('requests', 'memory') == '16Mi', 'bootstrap memory request drifted')
  assert!(resources.dig('limits', 'cpu') == '100m', 'bootstrap CPU limit drifted')
  assert!(resources.dig('limits', 'memory') == '64Mi', 'bootstrap memory limit drifted')
  true
end

def expect_failure!(name, main_kustomization, bootstrap_kustomization, job)
  yield main_kustomization, bootstrap_kustomization, job
  validate_contract!(
    main_kustomization: main_kustomization,
    bootstrap_kustomization: bootstrap_kustomization,
    job: job
  )
  raise ContractError, "negative case unexpectedly passed: #{name}"
rescue ContractError => error
  raise if error.message.start_with?('negative case unexpectedly passed:')
  puts "negative-case=#{name} status=rejected reason=#{error.message}"
end

main_kustomization = load_yaml(MAIN_KUSTOMIZATION)
bootstrap_kustomization = load_yaml(BOOTSTRAP_KUSTOMIZATION)
job = load_yaml(BOOTSTRAP_JOB)

validate_contract!(
  main_kustomization: main_kustomization,
  bootstrap_kustomization: bootstrap_kustomization,
  job: job
)
puts 'meta-agent-bootstrap-contract status=valid'

if ARGV == ['--self-test']
  cases = {
    'steady-state-inclusion' => lambda do |main, _overlay, _job|
      main['resources'] << 'bootstrap/meta-agent'
    end,
    'extra-overlay-resource' => lambda do |_main, overlay, _job|
      overlay['resources'] << 'secret.yaml'
    end,
    'retry-enabled' => lambda do |_main, _overlay, candidate|
      candidate['spec']['backoffLimit'] = 1
    end,
    'missing-deadline' => lambda do |_main, _overlay, candidate|
      candidate['spec'].delete('activeDeadlineSeconds')
    end,
    'missing-ttl' => lambda do |_main, _overlay, candidate|
      candidate['spec'].delete('ttlSecondsAfterFinished')
    end,
    'mutable-image' => lambda do |_main, _overlay, candidate|
      candidate.dig('spec', 'template', 'spec', 'containers', 0)['image'] = 'curlimages/curl:latest'
    end,
    'plaintext-token' => lambda do |_main, _overlay, candidate|
      env = candidate.dig('spec', 'template', 'spec', 'containers', 0, 'env', 0)
      env.delete('valueFrom')
      env['value'] = 'forbidden'
    end,
    'extra-environment-variable' => lambda do |_main, _overlay, candidate|
      candidate.dig('spec', 'template', 'spec', 'containers', 0, 'env') << {
        'name' => 'UNREVIEWED', 'value' => 'true'
      }
    end,
    'target-drift' => lambda do |_main, _overlay, candidate|
      args = candidate.dig('spec', 'template', 'spec', 'containers', 0, 'args')
      args[0] = args[0].sub(EXPECTED_TARGET, 'wrong-org/wrong-repository')
    end,
    'service-account-token' => lambda do |_main, _overlay, candidate|
      candidate.dig('spec', 'template', 'spec')['automountServiceAccountToken'] = true
    end,
    'host-network' => lambda do |_main, _overlay, candidate|
      candidate.dig('spec', 'template', 'spec')['hostNetwork'] = true
    end,
    'extra-container' => lambda do |_main, _overlay, candidate|
      containers = candidate.dig('spec', 'template', 'spec', 'containers')
      containers << deep_copy(containers.first)
    end,
    'privilege-escalation' => lambda do |_main, _overlay, candidate|
      candidate.dig('spec', 'template', 'spec', 'containers', 0, 'securityContext')['allowPrivilegeEscalation'] = true
    end,
    'unbounded-temporary-storage' => lambda do |_main, _overlay, candidate|
      candidate.dig('spec', 'template', 'spec', 'volumes', 0, 'emptyDir').delete('sizeLimit')
    end
  }

  cases.each do |name, mutation|
    expect_failure!(name, deep_copy(main_kustomization), deep_copy(bootstrap_kustomization), deep_copy(job), &mutation)
  end
  puts "meta-agent-bootstrap-self-test status=passed cases=#{cases.length}"
end
