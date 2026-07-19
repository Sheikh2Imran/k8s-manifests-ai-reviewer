import yaml
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from state import ReviewState
from schema import AgentReviewResult, FinalConsolidatedReview

llm = ChatOpenAI(model="gpt-4o", temperature=0.0).with_structured_output(AgentReviewResult)

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
            "You are an expert DevSecOps Site Reliability Engineer specialized in Kubernetes security.\n"
            "Analyze the following sanitized manifests and diff changes. Look specifically for:\n"
            "1. Missing `securityContext` definitions (e.g. running as root, writable root filesystems).\n"
            "2. Insecure plain-text leaks, missing RBAC least-privilege policies, or over-exposed Secrets.\n"
            "3. Host namespaces usage or dangerous capabilities settings."
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
            "You are an expert Cloud Architect checking structural platform reliability.\n"
            "Analyze these manifests focusing specifically on:\n"
            "1. `Service` selector accuracy matching the corresponding `Deployment` labels.\n"
            "2. Deployments/CronJobs lacking required `livenessProbe`, `readinessProbe`, or `startupProbe` rules.\n"
            "3. Correct rollout strategy layouts (`maxSurge`, `maxUnavailable`) and valid standard cron syntax configurations."
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
            "You are a FinOps and GitOps Orchestration Engine specialized in continuous reconcile states.\n"
            "Examine the configs to verify:\n"
            "1. Explicit container resource allocations (`requests` and `limits` for CPU/Memory).\n"
            "2. Environmental bindings: If a Deployment references a `ConfigMapKeyRef` or `secretKeyRef`, "
            "ensure the corresponding key actually exists inside the provided ConfigMap or Secret manifest data structure.\n"
            "3. Identify orphaned configurations (e.g. Services with no real backing deployment)."
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
    final_llm = ChatOpenAI(model="gpt-4o", temperature=0.0).with_structured_output(FinalConsolidatedReview)

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
