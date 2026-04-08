from __future__ import annotations

from uuid import uuid4

from langsmith import Client, evaluate

from backend.agent_service import AgentService
from backend.ragflow_gateway import RAGFlowGateway

DATASET_NAME = "MY_agent_eval_dataset"


def ensure_dataset(client: Client):
    try:
        return client.read_dataset(dataset_name=DATASET_NAME)
    except Exception:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="Minimal evaluation set for MY_agent.",
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"question": "为什么这个项目要同时用 LangChain、RAGFlow 和 LangSmith？"},
                    "outputs": {"expected_answer": "因为三者分别负责 agent 编排、知识库检索能力和追踪评估闭环。"},
                },
                {
                    "inputs": {"question": "LangSmith 在这个项目里主要用来做什么？"},
                    "outputs": {"expected_answer": "用来做 tracing、debug 和 evaluation。"},
                },
            ],
        )
        return dataset


def target_function(inputs: dict) -> dict:
    service = AgentService(gateway=RAGFlowGateway())
    result = service.answer(
        message=inputs["question"],
        session_id=f"eval_{uuid4().hex}",
    )
    return {"answer": result["answer"]}


def simple_overlap_evaluator(run_outputs: dict, reference_outputs: dict) -> bool:
    answer = str(run_outputs.get("answer", "")).strip()
    reference = str(reference_outputs.get("expected_answer", "")).strip()
    if not answer:
        return False
    if not reference:
        return True

    answer_chars = {char for char in answer if not char.isspace()}
    reference_chars = {char for char in reference if not char.isspace()}
    overlap = len(answer_chars & reference_chars) / max(1, len(reference_chars))
    return overlap >= 0.2


if __name__ == "__main__":
    client = Client()
    dataset = ensure_dataset(client)
    evaluate(
        target_function,
        data=dataset.name,
        evaluators=[simple_overlap_evaluator],
        experiment_prefix="MY_agent_mvp",
    )

