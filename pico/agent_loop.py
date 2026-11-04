"""Agent control loop extracted from the runtime facade."""

import time

from .checkpoint import CHECKPOINT_NONE_STATUS, CHECKPOINT_PARTIAL_STALE_STATUS, CHECKPOINT_WORKSPACE_MISMATCH_STATUS
from .task_state import TaskState
from .completion import CompletionGate
from .workspace import clip, now


class AgentLoop:
    def __init__(self, agent):
        self.agent = agent

    def _persist_model_failure(self, task_state, user_message, exc, run_started_at, prompt_metadata):
        agent = self.agent
        error_text = agent.redact_text(str(exc))
        final = f"Model request failed: {error_text}"
        task_state.stop_model_error(final)
        from .native_context import ContextBudgetExceeded
        if isinstance(exc, ContextBudgetExceeded):
            task_state.stop_reason = 'context_budget_exceeded'
            prompt_metadata['native_context'] = exc.audit
        agent.run_store.write_task_state(task_state)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger="model_error")
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "model_failed",
            {
                "error": error_text,
                "error_type": type(exc).__name__,
                "failure_stage": 'context_preparation' if isinstance(exc, ContextBudgetExceeded) else 'model_request',
                "completion_metadata": dict(agent.last_completion_metadata),
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "checkpoint_id": checkpoint["checkpoint_id"],
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.last_prompt_metadata = dict(prompt_metadata)
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))

    def _request_model(self, task_state, user_message, prompt, prompt_metadata, run_started_at, purpose):
        agent = self.agent
        if agent.capabilities:
            agent.cancellation.check()
            from .extensions import PolicyChanged
            if agent.capabilities.hooks.fingerprint() != agent.capabilities.policy_at_start:
                raise PolicyChanged('Host policy changed; start a new run so revoked historical content is not resent')
        conversation = getattr(agent.model_client, 'native_conversation', None)
        if conversation is not None:
            agent.session['working_state']['stale_paths'] = list(agent.resume_state.get('stale_paths', []))
            conversation.context_state = agent.redact_artifact(agent.session.get('working_state', {}))
            conversation.context_state['plan'] = agent.session.get('plan', [])
            if agent.capabilities:
                conversation.context_rules = agent.redact_text(agent.capabilities.refresh_rules(
                    getattr(agent.capabilities, 'active_path', agent.workspace.cwd)))
            query = user_message
            if agent.capabilities:
                failures = agent.session.get('working_state', {}).get('failed_attempts', [])
                if failures:
                    query = (query + '\n' + failures[-1].get('evidence', ''))[:5000]
            notes = (agent.memory.retrieval_candidates(query) if agent.feature_enabled('memory') and agent.feature_enabled('relevant_memory') else [])
            conversation.context_memory = agent.redact_artifact([
                {'text': n['text'], 'source': n.get('source'), 'kind': n.get('kind'),
                 'evidence': n.get('evidence', []), 'version': n.get('version'),
                 'trust': 'verified_source_or_operator' if n.get('kind') == 'verified' else 'unverified_legacy_note'}
                for n in notes])
        agent.emit_trace(
            task_state,
            "model_requested",
            {
                "attempts": task_state.attempts,
                "tool_steps": task_state.tool_steps,
                "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                "purpose": purpose,
            },
        )
        prompt_cache_key = None
        prompt_cache_retention = None
        if getattr(agent.model_client, "supports_prompt_cache", False):
            prompt_cache_key = prompt_metadata.get("prompt_cache_key")
            prompt_cache_retention = "in_memory"
        model_started_at = time.monotonic()
        transport_hook = hasattr(agent.model_client, 'on_transport_event')
        previous_hook = getattr(agent.model_client, 'on_transport_event', None)
        previous_delta = getattr(agent.model_client, 'on_delta', None)
        if agent.capabilities and hasattr(agent.model_client, 'on_delta'):
            agent.model_client.on_delta = lambda text: agent.emit_trace(task_state, 'model_delta', {'text': text})
        if transport_hook:
            def observe_transport(event):
                agent.emit_trace(task_state, 'model_transport', {'purpose':purpose, **event})
                if previous_hook:
                    previous_hook(event)
            agent.model_client.on_transport_event = observe_transport
        try:
            raw = agent.model_client.complete(
                prompt,
                agent.max_new_tokens,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
            )
        except Exception as exc:
            from .control import Cancelled
            if isinstance(exc, Cancelled) or agent.cancellation.event.is_set():
                raise Cancelled('Cancelled during model request; usage may be unknown') from exc
            completion_metadata = dict(getattr(agent.model_client, "last_completion_metadata", {}) or {})
            if completion_metadata:
                prompt_metadata.update(completion_metadata)
            agent.last_completion_metadata = completion_metadata
            agent.last_prompt_metadata = prompt_metadata
            self._persist_model_failure(task_state, user_message, exc, run_started_at, prompt_metadata)
            raise
        finally:
            if transport_hook:
                agent.model_client.on_transport_event = previous_hook
            if agent.capabilities and hasattr(agent.model_client, 'on_delta'):
                agent.model_client.on_delta = previous_delta
        completion_metadata = dict(getattr(agent.model_client, "last_completion_metadata", {}) or {})
        if completion_metadata:
            prompt_metadata.update(completion_metadata)
        agent.last_completion_metadata = completion_metadata
        agent.last_prompt_metadata = prompt_metadata
        if hasattr(raw, 'action_kind'):
            kind, payload = raw.action_kind, raw.action
        else:
            kind, payload = agent.parse(raw)
        agent.emit_trace(
            task_state,
            "model_parsed",
            {
                "kind": kind,
                "completion_metadata": completion_metadata,
                "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                "purpose": purpose,
            },
        )
        return raw, kind, payload

    def _finish_success(self, task_state, user_message, final, run_started_at):
        agent = self.agent
        if agent.capabilities:
            agent.cancellation.check()
        agent.record({"role": "assistant", "content": final, "created_at": now()})
        task_state.finish_success(final)
        if agent.feature_enabled('memory'):
            agent.promote_durable_memory(user_message, final)
        if agent.capabilities and agent.feature_enabled('memory'):
            experience = agent.capabilities.experience(task_state)
            if experience:
                agent.emit_trace(task_state, 'experience_recorded', {'memory_id': experience['id'], 'evidence': experience['evidence']})
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger="run_finished")
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": "run_finished",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        return final

    def run(self, user_message):
        agent = self.agent
        if agent.capabilities:
            agent.cancellation.check()
        completion = CompletionGate(agent)
        run_started_at = time.monotonic()
        agent.memory.set_task_summary(user_message)
        previous_working_state = agent.session.get('working_state', {})
        if agent.capabilities:
            from .evidence import invalidate_plan
            invalidated = invalidate_plan(agent)
            if invalidated:
                agent.session['plan_invalidations'] = invalidated
        agent.session['working_state'] = {'goal': user_message[:300], 'changed_files': [],
                                          'failed_attempts': [], 'verification': {'status': 'not_run'}}
        agent.record({"role": "user", "content": user_message, "created_at": now()})

        task_state = TaskState.create(run_id=agent.new_run_id(), task_id=agent.new_task_id(), user_request=user_message)
        task_state.resume_status = agent.resume_state.get("status", CHECKPOINT_NONE_STATUS)
        agent.current_task_state = task_state
        agent.current_run_dir = agent.run_store.start_run(task_state)
        native = bool(getattr(agent.model_client, 'supports_native_tools', False))
        if native:
            import json
            # Do not seed a new run with unbounded, potentially stale raw tool
            # transcripts. Keep user instructions intact and carry structured
            # historical state; fresh source facts arrive through retrieval.
            instructions = '\n\n'.join('Previous user instruction:\n' + item['content']
                for item in agent.session['history'][:-1] if item.get('role') == 'user')
            carryover = {k: previous_working_state[k] for k in ('goal', 'changed_files', 'failed_attempts') if k in previous_working_state}
            agent.model_client.begin_native_run(
                agent.tools,
                agent.redact_text(agent.workspace.text() + '\n\n' + (agent.memory_text() if agent.feature_enabled('memory') else 'Memory: disabled') + '\n\n' + instructions +
                    '\n\nHistorical task state (not a current verification):\n' + json.dumps(carryover, ensure_ascii=False)),
                user_message, agent.verification_command)
            conversation = getattr(agent.model_client, 'native_conversation', None)
            if conversation is not None:
                conversation.context_budget = agent.native_context_policy
                if agent.capabilities:
                    conversation.batch_limit = 8
                    conversation.messages[0]['content'] = conversation.messages[0]['content'].replace(
                        'Send exactly one function call per step.',
                        'You may return at most 8 native calls per request; execution is ordered, budgeted and individually approved. ')
                    conversation.messages[0]['content'] += (' Use code_search or symbol_info to navigate. '
                        'Persist a small plan for multi-step tasks. Prose memories stay candidates until operator confirmation. '
                        'Use evidence IDs to recover details after compaction. Available skills (load only when relevant): ' +
                        str(agent.capabilities.skills))
                    agent.model_client.cancellation = agent.cancellation
        agent.emit_trace(
            task_state,
            "run_started",
            {
                "task_id": task_state.task_id,
                "user_request": clip(user_message, 300),
            },
        )

        tool_steps = 0
        attempts = 0
        max_attempts = max(agent.max_steps * 3, agent.max_steps + 4)

        # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
        # 1. 感知：重新组 prompt，把当前状态整理给模型看
        # 2. 决策：让模型返回一个工具调用，或一个最终答案
        # 3. 行动：如果是工具调用，就执行工具
        # 4. 记录：把结果写回 history / task_state / trace / memory
        # 然后进入下一轮，直到停机条件满足
        while tool_steps < agent.max_steps and attempts < max_attempts:
            if agent.capabilities:
                agent.cancellation.check()
            agent.remaining_tool_steps = agent.max_steps - tool_steps
            conversation = getattr(agent.model_client, 'native_conversation', None) if native else None
            if conversation is not None and hasattr(conversation, 'set_budget'):
                conversation.set_budget(agent.remaining_tool_steps)
            attempts += 1
            task_state.record_attempt()
            agent.run_store.write_task_state(task_state)
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
            agent.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="freshness_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            elif prompt_metadata.get("resume_status") == CHECKPOINT_WORKSPACE_MISMATCH_STATUS:
                agent.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(prompt_metadata.get("runtime_identity_mismatch_fields", [])),
                    },
                )
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="workspace_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "workspace_mismatch",
                    },
                )
            if prompt_metadata.get("budget_reductions"):
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="context_reduction")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            raw, kind, payload = self._request_model(
                task_state,
                user_message,
                prompt,
                prompt_metadata,
                run_started_at,
                purpose="action",
            )

            if kind in {'tool', 'tools'}:
                actions = payload if kind == 'tools' else [payload]
                for action in actions:
                    if agent.cancellation.event.is_set():
                        if native:
                            while conversation.pending:
                                agent.model_client.observe_native_tool('Not executed: operator cancellation')
                        agent.cancellation.check()
                    if tool_steps >= agent.max_steps:
                        if native:
                            agent.model_client.observe_native_tool('Not executed: tool budget exhausted')
                        agent.emit_trace(task_state, 'tool_not_executed', {'name': action.get('name'), 'reason': 'budget_exhausted'})
                        continue
                    agent.remaining_tool_steps = agent.max_steps - tool_steps
                    tool_steps += 1
                    self._execute_action(action, task_state, completion, native, user_message)
                continue

            if kind == "retry":
                if native:
                    agent.model_client.native_notice(payload)
                agent.record({"role": "assistant", "content": payload, "created_at": now()})
                agent.run_store.write_task_state(task_state)
                continue

            final = (payload or raw).strip()
            problem = completion.problem()
            if problem:
                agent.emit_trace(task_state, 'completion_rejected', dict(completion.state))
                agent.record({'role':'assistant', 'content':problem, 'created_at':now()})
                if native:
                    agent.model_client.native_notice(problem)
                if completion.state['status']=='blocked':
                    return self._finish_stopped(task_state, user_message, 'verification_incomplete', problem, run_started_at)
                continue
            return self._finish_success(task_state, user_message, final, run_started_at)

        if tool_steps >= agent.max_steps:
            if agent.capabilities:
                agent.cancellation.check()
            if native:
                agent.model_client.native_notice('Tool budget exhausted. Do not call tools. Give a concise final answer based on actual evidence.')
            task_state.record_attempt()
            agent.run_store.write_task_state(task_state)
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
            prompt += (
                "\n\nRuntime notice: the tool budget is exhausted. Do not call another tool. "
                "Use the evidence already present in the tool history and return exactly one "
                "non-empty <final>...</final> answer."
            )
            prompt_metadata["finalization"] = True
            agent.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                    "purpose": "finalization",
                },
            )
            raw, kind, payload = self._request_model(
                task_state,
                user_message,
                prompt,
                prompt_metadata,
                run_started_at,
                purpose="finalization",
            )
            if kind == "final":
                final = (payload or raw).strip()
                problem = completion.problem()
                if not problem:
                    return self._finish_success(task_state, user_message, final, run_started_at)
                agent.emit_trace(task_state, 'completion_rejected', dict(completion.state))
                return self._finish_stopped(task_state, user_message, 'verification_incomplete', problem, run_started_at)
            if native and kind in {'tool', 'tools'}:
                conversation = getattr(agent.model_client, 'native_conversation', None)
                while conversation and conversation.pending:
                    agent.model_client.observe_native_tool('Not executed: finalization has no tool budget')

        if attempts >= max_attempts and tool_steps < agent.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            task_state.stop_step_limit(final)
        return self._finish_stopped(task_state, user_message, task_state.stop_reason, final, run_started_at)

    def _execute_action(self, payload, task_state, completion, native, user_message):
        agent = self.agent
        name, args = payload.get('name', ''), payload.get('args', {})
        task_state.record_tool(name)
        if agent.capabilities and args.get('path'):
            try:
                path = agent.path(args['path'])
            except ValueError:
                path = None
            if path is not None:
                agent.capabilities.refresh_rules(path)
                agent.capabilities.active_path = path
        started = time.monotonic()
        tool_result = agent.execute_tool(name, args)
        completion.observe(name, args, tool_result.metadata)
        result = tool_result.content
        working = agent.session['working_state']
        working['verification'] = dict(completion.state)
        working['changed_files'] = sorted(set(working['changed_files']) | set(tool_result.metadata.get('affected_paths', [])))
        if tool_result.metadata.get('tool_status') != 'ok':
            working['failed_attempts'].append({'tool': name, 'path': args.get('path'),
                'error': tool_result.metadata.get('tool_error_code'), 'evidence': result[:400]})
            working['failed_attempts'] = working['failed_attempts'][-6:]
        if native:
            agent.model_client.observe_native_tool(result)
        agent.record({'role': 'tool', 'name': name, 'args': args, 'content': result, 'created_at': now(),
                      'source_paths': tool_result.metadata.get('source_paths', [])})
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(task_state, 'tool_executed', {'name': name, 'args': args,
            'result': clip(result, 12000 if name == 'run_shell' else 500),
            'duration_ms': int((time.monotonic()-started)*1000), **dict(tool_result.metadata or {})})
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger='tool_executed')
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(task_state, 'checkpoint_created', {'checkpoint_id': checkpoint['checkpoint_id'], 'trigger': 'tool_executed'})

    def _finish_stopped(self, task_state, user_message, reason, final, run_started_at):
        agent = self.agent
        task_state.stop(reason, final_answer=final)
        agent.record({"role": "assistant", "content": final, "created_at": now()})
        agent.run_store.write_task_state(task_state)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger=task_state.stop_reason or "run_stopped")
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": task_state.stop_reason or "run_stopped",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        return final
