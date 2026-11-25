import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "pd-chatbot";
export default defineRailway(() => {
  const pdChatbot = service("pd-chatbot", {
    source: github("wilsontr/pd-chatbot", { branch: "main", checkSuites: false }),
    build: { buildCommand: "pip install -r requirements.txt && python embed_and_index.py", buildEnvironment: "V3", builder: "NIXPACKS" },
    start: "newrelic-admin run-program uvicorn main:app --host 0.0.0.0 --port $PORT",
    healthcheck: "/health",
    // Carried over from railway.json; `config migrate` does not translate it.
    deploy: { restartPolicyType: "ON_FAILURE" },
    replicas: { "sfo": 1 },
  });

  return project("pd chatbot", {
    variables: { managed: false },
    resources: [pdChatbot],
  });
});
