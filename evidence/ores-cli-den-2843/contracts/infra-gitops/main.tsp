import "@typespec/json-schema";
using TypeSpec.JsonSchema;

namespace Ores.InfraGitOps;

@id("InfraGitOpsContract")
model InfraGitOpsContract {
  supabaseRoot: string;
  neonRoot: string;
  secretPolicy: string;
  promotionPolicy: string;
}
