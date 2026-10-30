"""Validate durable provider-import intents against the original journal and accounting."""
import hashlib
import json
import math
import copy
from datetime import datetime
from pathlib import Path

from .recovery import canonical_hash
from ..agents.llm_provider import ResponsesAPIProvider, parse_llm_provider_config


def settled_status(before, reservation_id, usage):
    state = copy.deepcopy(before)
    reservation = state['pending_reservations'].pop(reservation_id)
    for field, value in (('input_tokens', usage['prompt_tokens']), ('output_tokens', usage['completion_tokens']),
                         ('estimated_cost_usd', usage['estimated_cost_usd'])):
        reserve_field = 'cost_usd' if field == 'estimated_cost_usd' else field
        pending_field = 'pending_cost_usd' if field == 'estimated_cost_usd' else 'pending_' + field
        state[pending_field] -= reservation[reserve_field]
        state['known_' + field] += value
        if state['total_' + field] is not None:
            state['total_' + field] += value
        admission = 'budget_admission_estimated_cost_usd' if field == 'estimated_cost_usd' else 'budget_admission_estimated_' + field
        state[admission] = state['known_' + field] + state[pending_field]
    state.pop('config_fingerprint', None)
    return state


def verified_imports(path, journal_path, ledger, budget, rows, events):
    intents = [row for row in rows if row.get('kind') == 'provider_import_intent_v1']
    journal = journal_path.read_text(encoding='utf-8')
    verified = {}
    for intent in intents:
        plan_hash = intent.get('plan_sha256')
        if not isinstance(plan_hash, str) or len(plan_hash) != 64 or any(c not in '0123456789abcdef' for c in plan_hash):
            raise ValueError('provider import intent hash is invalid')
        transaction_path = path / 'provider_imports' / plan_hash / 'transaction.json'
        try:
            transaction = json.loads(transaction_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError('provider import transaction is unreadable') from exc
        plan = transaction.get('plan')
        if (not isinstance(plan, dict) or canonical_hash(plan) != plan_hash
                or canonical_hash(transaction) != intent.get('transaction_sha256')):
            raise ValueError('provider import transaction differs from durable intent')
        before = transaction.get('journal_before')
        if not isinstance(before, str) or not journal.startswith(before):
            raise ValueError('provider import journal prefix changed')
        try:
            preceding = [json.loads(line) for line in before.splitlines()]
        except ValueError as exc:
            raise ValueError('provider import journal prefix is invalid') from exc
        if len(preceding) != intent.get('sequence') or preceding != rows[:len(preceding)]:
            raise ValueError('provider import intent is not bound to the preceding journal')
        checkpoint = transaction.get('checkpoint_before')
        if (not isinstance(checkpoint, dict) or checkpoint.get('identity') != plan.get('identity')
                or not checkpoint.get('halt_required')
                or checkpoint.get('sha256') != canonical_hash({k: v for k, v in checkpoint.items() if k != 'sha256'})):
            raise ValueError('provider import is not bound to a halted checkpoint')
        prefix_hash = transaction.get('ledger_sha256')
        digest = hashlib.sha256()
        matches = int(digest.hexdigest() == prefix_hash)
        for line in ledger.splitlines(keepends=True):
            digest.update(line)
            matches += int(digest.hexdigest() == prefix_hash)
        if matches != 1:
            raise ValueError('provider import request ledger prefix changed')
        original_budget = transaction.get('budget_before')
        if not isinstance(original_budget, dict) or not isinstance(original_budget.get('pending_reservations'), dict):
            raise ValueError('provider import prior budget is invalid')
        event_prefix = transaction.get('budget_events_before')
        if not isinstance(event_prefix, str):
            raise ValueError('provider import budget event prefix is missing')
        try:
            previous_events = [json.loads(line) for line in event_prefix.splitlines()]
        except ValueError as exc:
            raise ValueError('provider import budget event prefix is invalid') from exc
        if events[:len(previous_events)] != previous_events:
            raise ValueError('provider import budget event prefix changed')
        observations = plan.get('observations')
        if not isinstance(observations, list) or not observations:
            raise ValueError('provider import observations are missing')
        routes, agents = plan.get('provider_routes'), plan.get('provider_agents')
        if (not isinstance(routes, list) or not isinstance(agents, list) or len(routes) != len(agents)
                or len(set(agents)) != len(agents)
                or (plan['identity'].get('routes_sha256') is not None
                    and canonical_hash(routes) != plan['identity']['routes_sha256'])):
            raise ValueError('provider import routes differ from recovery identity')
        configuration = plan.get('provider_configuration')
        if plan['identity'].get('configuration_sha256') is not None:
            if (not isinstance(configuration, dict) or canonical_hash(configuration) != plan['identity']['configuration_sha256']
                    or [item.get('agent_id') for item in configuration.get('agents', [])] != agents):
                raise ValueError('provider import agent configuration differs from recovery identity')
        if plan.get('selected_attempt_id') != observations[0].get('attempt_id'):
            raise ValueError('provider import selected attempt changed')
        try:
            game, phase, _, country, agent, method = observations[0]['decision_id'].split('/')
            saved = plan['validation_context']
            context = saved['context']
            if (canonical_hash(saved) != plan.get('validation_context_sha256') or saved.get('method') != method
                    or context.get('game_id') != game or context.get('phase_id') != phase
                    or context.get('agent_id') != agent or context.get('country_assignment') != country):
                raise ValueError('provider import validation context changed')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('provider import validation context is unavailable or changed') from exc
        expected_budget = copy.deepcopy(original_budget)
        event_cursor = len(previous_events)
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValueError('provider import observation is invalid')
            attempt = observation.get('attempt_id')
            if not isinstance(attempt, str) or attempt in verified:
                raise ValueError('provider import attempt is duplicated')
            identity = (observation.get('decision_id'), observation.get('prompt_hash'))
            try:
                route_agent = identity[0].split('/')[4]
                provider_config = parse_llm_provider_config(routes[agents.index(route_agent)])
            except (ValueError, IndexError, AttributeError) as exc:
                raise ValueError('provider import route is unavailable') from exc
            source_response, source_input = observation.get('source_response'), observation.get('source_input')
            if (not isinstance(source_response, dict) or not isinstance(source_input, dict)
                    or canonical_hash(source_response) != observation.get('response_sha256')
                    or canonical_hash(source_input) != observation.get('input_sha256')
                    or source_response.get('id') != observation.get('response_id')
                    or source_response.get('status') != 'completed' or source_response.get('error')
                    or source_response.get('incomplete_details')):
                raise ValueError('provider import source evidence differs from intent')
            data = source_input.get('data')
            if (source_input.get('has_more') or not isinstance(data, list) or len(data) != 1
                    or data[0].get('role') != 'user' or not isinstance(data[0].get('content'), list)
                    or not data[0]['content'] or any(block.get('type') not in {'input_text', 'text'}
                        or not isinstance(block.get('text'), str) for block in data[0]['content'])):
                raise ValueError('provider import input evidence is invalid')
            prompt = ''.join(block['text'] for block in data[0]['content'])
            if hashlib.sha256(prompt.encode()).hexdigest() != identity[1]:
                raise ValueError('provider import input does not match the actual prompt')
            usage = observation.get('usage')
            pricing = observation.get('provider_pricing')
            source_usage = ResponsesAPIProvider._normalize_usage(source_response.get('usage'))
            response = observation.get('response')
            if (not isinstance(usage, dict) or not isinstance(response, dict)
                    or any(isinstance(usage.get(k), bool) or not isinstance(usage.get(k), int)
                           or usage[k] < 0 or usage[k] != source_usage.get(k)
                           for k in ('input_tokens', 'output_tokens', 'total_tokens'))
                    or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']
                    or isinstance(usage.get('estimated_cost_usd'), bool)
                    or not isinstance(usage.get('estimated_cost_usd'), (int, float))
                    or not math.isfinite(usage['estimated_cost_usd']) or usage['estimated_cost_usd'] < 0
                    or not isinstance(pricing, dict) or any(isinstance(pricing.get(k), bool)
                        or not isinstance(pricing.get(k), (int, float)) or not math.isfinite(pricing[k]) or pricing[k] < 0
                        for k in ('input_cost_per_1k_tokens', 'output_cost_per_1k_tokens'))
                    or pricing != {'input_cost_per_1k_tokens': provider_config.input_cost_per_1k_tokens,
                                   'output_cost_per_1k_tokens': provider_config.output_cost_per_1k_tokens}
                    or usage['estimated_cost_usd'] != round((usage['input_tokens'] * pricing['input_cost_per_1k_tokens']
                        + usage['output_tokens'] * pricing['output_cost_per_1k_tokens']) / 1000, 10)
                    or response.get('content') != ResponsesAPIProvider._extract_text(source_response)
                    or response.get('model') != source_response.get('model') or source_response.get('model') != provider_config.model
                    or (response.get('raw') or {}).get('usage') != usage
                    or ((response.get('raw') or {}).get('response') or {}).get('id') != source_response['id']
                    or (response.get('raw') or {}).get('recovered_from_provider') is not True):
                raise ValueError('provider import response or usage evidence differs')
            related = [r for r in preceding if r.get('attempt_id') == attempt]
            starts = [r for r in related if r.get('kind') == 'dispatch_prepared']
            dispatches = [r for r in related if r.get('kind') == 'dispatch']
            outcomes = [r for r in related if r.get('kind') == 'attempt']
            if (len(starts) != 1 or len(dispatches) != 1 or len(outcomes) != 1
                    or outcomes[0].get('outcome') != 'unknown'
                    or any((r.get('decision_id'), r.get('prompt_hash')) != identity for r in related if r.get('kind') in {'dispatch_prepared', 'dispatch', 'attempt'})
                    or not starts[0]['sequence'] < dispatches[0]['sequence'] < outcomes[0]['sequence']):
                raise ValueError('provider import does not match the exact original unknown')
            response_id = source_response['id']
            created, completed = source_response.get('created_at'), source_response.get('completed_at')
            if (not isinstance(response_id, str) or not response_id.startswith('resp_')
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (created, completed)) or completed < created
                    or not datetime.fromisoformat(starts[0]['started_at']).timestamp() - 1 <= created
                           <= datetime.fromisoformat(dispatches[0]['started_at']).timestamp() + 1
                    or observation.get('created_at') != created or observation.get('completed_at') != completed
                    or observation.get('provider_elapsed_seconds') != completed - created
                    or (starts[0].get('provider') or {}).get('requested_model') != source_response.get('model')):
                raise ValueError('provider import model or timing differs from original request')
            reservation_id = observation.get('reservation_id')
            original = original_budget['pending_reservations'].get(reservation_id)
            if not isinstance(original, dict) or original.get('status') != 'uncertain':
                raise ValueError('provider import original reservation is missing')
            later = [r for r in rows[intent['sequence'] + 1:] if r.get('kind') == 'attempt' and r.get('attempt_id') == attempt]
            if len(later) > 1 or any(r.get('outcome') != 'received' or r.get('response') != observation.get('response')
                    or r.get('usage') != observation.get('usage') or (r.get('decision_id'), r.get('prompt_hash')) != identity for r in later):
                raise ValueError('provider import received response differs from intent')
            settled = reservation_id not in budget.get('pending_reservations', {})
            if later and not settled:
                raise ValueError('provider import response precedes reservation settlement')
            if settled:
                parts = identity[0].split('/')
                expected_budget = settled_status(expected_budget, reservation_id, observation['usage'])
                matches = [(index, event) for index, event in enumerate(events[event_cursor:], event_cursor)
                           if event.get('event_type') == 'budget_usage_update'
                           and event.get('experiment_id') == budget.get('experiment_id')
                           and all(event.get('payload', {}).get(k) == v for k, v in
                                   {'game_id': parts[0], 'phase_id': parts[1], 'agent_id': parts[4], 'method': parts[5]}.items())
                           and event.get('payload', {}).get('usage') == observation.get('usage')
                           and event.get('payload', {}).get('totals') == expected_budget]
                markers = [row for row in rows[intent['sequence'] + 1:]
                           if row.get('kind') == 'provider_import_settlement_v1'
                           and row.get('plan_sha256') == plan_hash and row.get('attempt_id') == attempt]
                if len(markers) > 1:
                    raise ValueError('provider import settlement marker is duplicated')
                if markers:
                    matches = [(index, event) for index, event in matches
                               if index == markers[0].get('event_index')
                               and canonical_hash(event) == markers[0].get('event_sha256')]
                elif later:
                    raise ValueError('provider import response lacks a settlement marker')
                if len(matches) != 1:
                    raise ValueError('provider import settlement event is missing or ambiguous')
                event_cursor = matches[0][0] + 1
            elif budget['pending_reservations'].get(reservation_id) != original:
                raise ValueError('provider import reservation changed')
            verified[attempt] = {'observation': observation, 'settled': settled, 'received': bool(later)}
    return verified
