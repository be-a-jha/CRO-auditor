"""Batch queue logic with a fake API client (no key, no cost): requests from several pages go out as one batch, pages
that drop out (failed early / rules only) don't stall it, results reach the right page, and errors are per request.

    python tests/test_batch_queue.py
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cro import llm as llm_mod  # noqa: E402


class FakeBatches:
    def __init__(self):
        self.created = []

    async def create(self, requests):
        self.created.append(requests)
        self.reqs = requests
        self.polls = 0
        return NS(id=f"batch_{len(self.created)}", processing_status="in_progress")

    async def retrieve(self, bid):
        self.polls += 1
        return NS(id=bid, processing_status="ended" if self.polls >= 2 else "in_progress")

    async def results(self, bid):
        async def gen():
            for r in self.reqs:
                text = r["params"]["messages"][0]["content"][0]["text"]
                if "FAIL" in text:
                    yield NS(custom_id=r["custom_id"], result=NS(type="errored", error=NS(error=NS(message="overloaded"))))
                    continue
                msg = NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"echo": text}))],
                         usage=NS(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=800))
                yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=msg))
        return gen()


async def main():
    llm = llm_mod.LLM.__new__(llm_mod.LLM)
    llm.model, llm.fast_model, llm.effort, llm.dry_run_dir, llm.calls = "claude-sonnet-5-5", "claude-haiku-5-5", "low", None, []
    fake = FakeBatches()
    llm.client = NS(messages=NS(batches=fake))
    llm.batch = llm_mod.BatchQueue(llm, idle_s=5, poll_s=0.05)
    llm.batch.expect(4)  # four pages in the run

    async def page(i, text, delay):
        await asyncio.sleep(delay)
        if text is None:          # this page failed before its audit
            llm.batch.skip(i)
            return None
        try:
            return await llm.call("sys", [{"type": "text", "text": text}], {"type": "object"}, tag=f"p{i}.audit",
                                  batchable=True, batch_key=i)
        except llm_mod.LLMError as e:
            return f"error: {e}"
        finally:
            llm.batch.skip(i)     # pipeline always calls this; must be a no-op after submitting

    res = await asyncio.wait_for(asyncio.gather(page(1, "page one", 0), page(2, None, 0.05), page(3, "page three", 0.1),
                                                page(4, "FAIL four", 0.15)), timeout=4)
    checks = {
        "one batch for the whole run": len(fake.created) == 1,
        "batch holds the 3 pages that reached the audit": len(fake.created[0]) == 3,
        "results routed to the right page": res[0] == {"echo": "page one"} and res[2] == {"echo": "page three"},
        "dropped page did not stall the batch": res[1] is None,
        "per-request error isolated": isinstance(res[3], str) and "overloaded" in res[3],
        "batch calls billed at 50%": all(c["batch"] for c in llm.calls) and
                                     abs(llm.calls[0]["cost"] - llm_mod.cost_of("claude-sonnet-5-5", llm.calls[0], False) / 2) < 1e-9,
        "requests carry no beta-only params": all("fallbacks" not in r["params"] and "betas" not in r["params"] for r in fake.created[0]),
    }
    w = max(map(len, checks))
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k:<{w}}")
    assert all(checks.values())
    print("  batch queue checks passed")


if __name__ == "__main__":
    asyncio.run(main())
