import ast
import os

with open('services.py', 'r', encoding='utf-8') as f:
    orig = f.read()

tree = ast.parse(orig)

# Get imports (top chunk)
imports = orig.split('def get_user_key')[0]

groups = {
    'analysis': ['_coerce_analysis_dict', '_extract_live_match_fields', 'ensure_analysis_shape', 'build_default_analysis', 'parse_live_data', 'get_fallback_live_matches', '_analysis_needs_refresh', 'build_payload', 'get_live_matches_context'],
    'chat': ['edson_chat', 'build_chat_response', 'shorten_chat_text', 'frontend_history_to_groq_messages', '_is_generic_no_data_reply', 'get_chat_db_context', 'get_fbref_db_context', 'get_web_context'],
    'odds': ['_get_live_odds_matches', '_select_match_for_cta', 'build_chat_cta', '_encode_sportingtech_body', '_extract_fixture_from_detail_payload', 'get_sportingtech_fixture_match_with_markets', '_build_upcoming_sporting_matches_with_markets', 'get_upcoming_matches_context', '_is_brazil_intent', '_is_brazil_row', '_score_market_name', '_pick_selection_for_market', '_pick_offer_from_markets', '_build_dynamic_cta_from_live_context'],
    'live': ['_ensure_live_matches_table', '_sync_live_matches_cache_from_api', '_get_live_matches_from_db', '_refresh_live_analyses_once', '_live_analyses_refresh_loop', '_startup_live_refresh_worker']
}

# we'll write all remaining uncategorized functions to `core/utils.py`
os.makedirs('core', exist_ok=True)
os.makedirs('analysis', exist_ok=True)
os.makedirs('chat', exist_ok=True)
os.makedirs('odds', exist_ok=True)
os.makedirs('live', exist_ok=True)

for group_name, funcs in groups.items():
    if not funcs: continue
    
    body_str = imports + "\n"
    for func_name in funcs:
        # find node
        node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
        if node:
            func_str = ast.get_source_segment(orig, node)
            body_str += func_str + "\n\n"
    
    with open(f'{group_name}/service.py', 'w', encoding='utf-8') as f:
        f.write(body_str)

# whatever is left goes to core/utils.py
categorized = set(sum(groups.values(), []))
all_funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
uncategorized = [n for n in all_funcs if n.name not in categorized]

utils_str = imports + "\n"
for node in uncategorized:
    func_str = ast.get_source_segment(orig, node)
    utils_str += func_str + "\n\n"

with open('core/utils.py', 'w', encoding='utf-8') as f:
    f.write(utils_str)

print('Extracted successfully.')
