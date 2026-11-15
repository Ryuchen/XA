import test from "node:test";
import assert from "node:assert/strict";
import { afterSaleHistoryKey } from "../src/business.ts";

test("aftersale history keeps distinct entries for one request and handles legacy rows", () => {
  const open = { client_request_id: "aftersale-same", status: "OPEN", created_at: "2026-11-06T01:00:00Z", resolved_at: null };
  const resolved = { ...open, status: "RESOLVED", resolved_at: "2026-11-06T02:00:00Z" };
  assert.notEqual(afterSaleHistoryKey(open, 0), afterSaleHistoryKey(resolved, 1));
  assert.notEqual(afterSaleHistoryKey({}, 0), afterSaleHistoryKey({}, 1));
  assert.match(afterSaleHistoryKey(open, 0), /aftersale-same:OPEN/);
  assert.match(afterSaleHistoryKey(resolved, 1), /RESOLVED/);
});
