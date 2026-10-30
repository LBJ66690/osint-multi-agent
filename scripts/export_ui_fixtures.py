"""Export public UI test fixtures from code; no model or search requests."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import server
import search_sources

async def main():
    values = {
        'search_source_options.json': search_sources.source_options(),
        'model_profile_options.json': await server.list_models(),
    }
    for name, value in values.items():
        (config.OUTPUT_DIR / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Created outputs/' + name)

if __name__ == '__main__':
    asyncio.run(main())
