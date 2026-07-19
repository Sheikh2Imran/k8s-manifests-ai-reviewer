import yaml
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from state import ReviewState
from schema import AgentReviewResult, FinalConsolidatedReview

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.0,
    timeout=60.0,
    max_retries=2
).with_structured_output(AgentReviewResult)

# Node 1: Sanitization
def preprocess_sanitizer_node(state: ReviewState) -> dict:
    sanitized = {}
    static_errors = []

    for file_path, contents in state["raw_manifests"].items():
        try:
            documents = list(yaml.safe_load_all(contents))
            processed_docs = []
            for doc in documents:
                if not doc:
                    continue
                if str(doc.get("kind")).strip().lower() == "secret":
                    if "data" in doc:
                        doc["data"] = {k: "<REDACTED_SECRET_HASH>" for k in doc["data"].keys()}
                    if "stringData" in doc:
                        doc["stringData"] = {k: "<REDACTED_SECRET_PLAIN>" for k in doc["stringData"].keys()}
                processed_docs.append(doc)

            sanitized[file_path] = yaml.dump_all(processed_docs)

        except Exception as e:
            static_errors.append(f"Syntax/Parsing error in {file_path}: {str(e)}")

    return {"sanitized_manifests": sanitized, "static_errors": static_errors}


# Node 2: Security Agent
def security_agent_node(state: ReviewState) -> dict:
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are an expert DevSecOps Platform Engineer.\n"
            "Review the manifests against our mandatory security rules. Flag a CRITICAL violation if:\n"
            "For every violation discovered, trace the structural path down to the exact input block and provide \n"
            "the corresponding file 'target_line' integer.\n"
            "1. A `Deployment` or `CronJob` does not explicitly declare `imagePullSecrets` to fetch images securely.\n"
            "2. Critical sensitive strings are hardcoded in the plain `env:` array instead of using `secretRef` mappings.\n"
            "3. Containers lack an explicit `securityContext` setting (e.g. running as root, missing read-only root filesystems)."
        )),
        ("human", "Sanitized Manifests:\n{manifests}\n\nGit Diffs:\n{diffs}")
    ])

    chain = prompt | llm
    response: AgentReviewResult = chain.invoke({
        "manifests": yaml.dump(state["sanitized_manifests"]),
        "diffs": yaml.dump(state["git_diffs"])
    })
    return {"aggregated_violations": response.violations}


# Node 3: Reliability Agent
def reliability_agent_node(state: ReviewState) -> dict:
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a Principal SRE Architect. Our organization enforces a strict mandatory structural blueprint "
            "for every single Deployment manifest. You must validate the manifests against this baseline.\n"
            "For every violation discovered, trace the structural path down to the exact input block and provide \n"
            "the corresponding file 'target_line' integer (e.g. if livenessProbe is completely missing or blank, \n"
            "target the 'containers' structural declaration block line).\n\n"

            "CRITICAL RULES YOU MUST ENFORCE:\n"
            "1. **Labels & Selectors:** Both `metadata.labels` and `spec.selector.matchLabels` and `spec.template.metadata.labels` "
            "MUST strictly contain BOTH of these specific keys:\n"
            "   - `app.kubernetes.io/instance`\n"
            "   - `app.kubernetes.io/name`\n"
            "   If either key is missing in any of these three metadata sections, flag it as CRITICAL.\n\n"

            "2. **Conditional InitContainers Validation (Optional Gates):**\n"
            "   Deployments can legally have 0, 1, or 2 initContainers. They are NOT mandatory. However, IF they are present, "
            "   you must strictly validate their naming patterns and command structural compliance:\n"
            "   - Allowed initContainer names are strictly: `wait-for-dependency` and `db-migrations`.\n"
            "   - Every initContainer must define a non-empty `image` and non-empty `command` array.\n"
            "   - Placeholder/no-op command patterns are forbidden (e.g. `sleep infinity`, `tail -f /dev/null`).\n"
            "   - Flag a CRITICAL violation if an unknown initContainer name is present, or if required fields/command quality are invalid.\n\n"

            "3. **Health Observability Probes:** The primary application container MUST declare a full observability suite consisting of:\n"
            "   - `startupProbe`\n"
            "   - `livenessProbe`\n"
            "   - `readinessProbe`\n"
            "   If any of these three probes are missing, empty, or replaced with dots ('...'), flag it as CRITICAL.\n\n"

            "4. **Storage Topology (Conditional):** IF the deployment declares persistent storage (identified by the presence of "
            "`volumeMounts` or `persistentVolumeClaim` references), then the configuration MUST be complete:\n"
            "   - Every `volumeMounts` entry must map to a corresponding `volumes` declaration at the pod spec level.\n"
            "   - Volume sources must use `persistentVolumeClaim` with a valid `claimName` (not emptyDir or hostPath for production workloads).\n"
            "   - If NO storage is declared (fully stateless deployment), this rule does not apply. Only flag violations when storage is partially configured."
        )),
        ("human", "Sanitized Manifests:\n{manifests}\n\nGit Diffs:\n{diffs}")
    ])

    chain = prompt | llm
    response: AgentReviewResult = chain.invoke({
        "manifests": yaml.dump(state["sanitized_manifests"]),
        "diffs": yaml.dump(state["git_diffs"])
    })
    return {"aggregated_violations": response.violations}


# Node 4: Resource and Configuration Agent
def resource_agent_node(state: ReviewState) -> dict:
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a GitOps Orchestration Engine enforcing cluster efficiency.\n"
            "Analyze the manifests and trigger a CRITICAL violation if:\n"
            "For every violation discovered, trace the structural path down to the exact input block and provide \n"
            "the corresponding file 'target_line' integer\n"
            "1. **Resource Availability & Boundaries:**\n"
            "   - The container MUST explicitly define BOTH `requests` and `limits` for both CPU and Memory.\n"
            "   - The specific values can be variable, but the **Memory Request (`requests.memory`) MUST NOT exceed 1Gi** (1 Gigabytes / 1024Mi).\n"
            "   - Flag as CRITICAL if `resources`, `requests`, or `limits` are missing entirely, or if the memory request exceeds 1Gi.\n\n"

            "2. **Environmental Sourcing:** Ensure the deployment specifies an environment configuration using **both** "
            "`configMapRef` and `secretRef` globally inside `envFrom`, alongside explicit key-value parameters under `env:` "
            "(specifically verifying the mandatory `branch` tracking key is populated).\n\n"

            "3. **Reference Integrity: ** If a deployment references an external configuration name under `configMapRef` or `secretRef`, "
            "and that resource configuration file is included in this PR payload, verify that the names match perfectly."
        )),
        ("human", "Sanitized Manifests:\n{manifests}\n\nGit Diffs:\n{diffs}")
    ])

    chain = prompt | llm
    response: AgentReviewResult = chain.invoke({
        "manifests": yaml.dump(state["sanitized_manifests"]),
        "diffs": yaml.dump(state["git_diffs"])
    })
    return {"aggregated_violations": response.violations}


# Node 5: Final Orchestration Agent
def final_orchestration_node(state: ReviewState) -> dict:
    final_llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.0,
        timeout=60.0,
        max_retries=2
    ).with_structured_output(FinalConsolidatedReview)

    if state.get("static_errors"):
        return {
            "final_verdict": "REQUEST_CHANGES",
            "executive_summary": f"Pre-flight static manifest compilation failed immediately:\n" + "\n".join(
                state["static_errors"])
        }

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are the Lead Principal Infrastructure Architect reviewing systemic aggregated logs.\n"
            "Examine all parallel verification findings from the security, reliability, and configuration agents.\n"
            "Determine a final verdict. If ANY findings are marked as 'CRITICAL', you MUST declare a verdict of 'REQUEST_CHANGES'.\n"
            "Generate a highly concise, concrete corporate review message detailing blockers."
        )),
        ("human", "Collected Sub-Agent Observations:\n{violations}")
    ])

    violations_text = yaml.dump([v.dict() for v in state["aggregated_violations"]])
    chain = prompt | final_llm

    response: FinalConsolidatedReview = chain.invoke({"violations": violations_text})

    return {
        "final_verdict": response.verdict,
        "executive_summary": response.executive_summary
    }
