# Component promotion boundary

`control-plane/fleet.json` names the component paths that the canonical `mb-infra` repository will reconcile. This staging blueprint intentionally does not fabricate deployable databases, queues, services, workers, ingress, models, or observability stacks before their real images, charts, CRDs, and cluster prerequisites exist.

A component may be added under its declared path only when it has:

1. immutable image/chart/model references;
2. explicit namespace, service account, RBAC, resource, probe, and network-policy behavior;
3. a compatible `mb-interfaces` and database/schema window;
4. render/schema/policy tests and at least one committed invalid fixture;
5. documented local, staging, and production differences;
6. promotion, rollback, and ownership evidence.

The renderer still emits the exact child Application graph, but reports every missing component path as a promotion blocker. This preserves honest app-of-apps topology without pretending that placeholder workloads have converged.
