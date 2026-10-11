# Kestrel Task 4 demo transcript

Local synthetic narration; actual browser recording and captured pipeline evidence.

## intro (00:00:01,034)

This is Kestrel's register reconciliation for Task Four. The finance controller needs to compare a handwritten receiving register, the ERP goods received notes, and the storekeeper's Excel sheet. This demo replays the supplied fictional September data, then shows a human review and a safely handled missing source.

## inputs (00:00:16,772)

These are the two original register photographs. Together they contain twenty eight written rows. The ERP export contains twenty eight rows, while Excel contains thirty. Supplier spellings, date formats and units differ between sources, so explicit normalization happens before comparison. The original files are retained.

## extraction (00:00:34,099)

Both photos were genuinely read twice by Gemini two point five Flash. Each read found fourteen rows per page. The extraction cache records the provider, model and image hash. The blot on goods received note forty four twenty eight returned null in both reads. The system does not substitute the ERP's quantity. This recording reuses those verified model results.

## run (00:00:56,969)

This is captured output from running the actual pipeline in an isolated demo copy. It extracts or loads cached rows, normalizes them, reconciles the three sources, builds a ledger, checks completeness, and generates the controller message. Console delivery is used in this copy so demonstration actions do not send extra emails.

## proof (00:01:13,832)

The ledger contains eighty six unique source row identifiers. The outcome counts add back to twenty eight register rows, twenty eight ERP rows, and thirty Excel rows. Quantity totals are checked separately by source and unit. Duplicates and adjustments remain accounted for in the ledger, even though they are not counted as new matched receipts.

## exceptions (00:01:31,352)

The controller sees exceptions first. These include the ERP lot transposition, the Sunday date, forty six versus forty eight cartons, and the Excel fabric unit error. The report also identifies a missing ERP entry, the ERP only late entry, the sequence gap, the duplicate sheet receipt and the separate negative return adjustment.

## review (00:01:50,657)

This is the actual Streamlit review screen. Only two doubtful quantities appear. For forty four nineteen, the register clearly shows sixty crossed out and sixty six written above it. The reviewer sees the cropped photo, why it was flagged, and the values from the other sources. Every saved decision includes the reviewer's name.

## blot (00:02:10,968)

The second item is the ink blot. Its input is empty because the register quantity cannot be read with certainty. The other two sources say two hundred and fifty, but that does not establish what is written here. I leave this entry unresolved. A person must consult a legible source before confirming it.

## decision (00:02:31,332)

In the isolated copy, I enter Demo Reviewer and confirm the visible corrected value of sixty six. I add a short reason and save the decision. Then I rerun reconciliation with that decision applied. This demonstrates the review workflow without changing the real project's pending decisions or source files.

## after (00:03:12,463)

After the rerun, the corrected entry leaves the review queue. Its Excel quantity of sixty becomes a reported mismatch against sixty six in the register and ERP. Only the ink blotted quantity remains open. The completeness proof still ties, and the audit log records the reviewer and decision.

## failure (00:03:35,307)

Here is the required failure demonstration. I temporarily removed the ERP export from the isolated copy and ran the pipeline. It continued with the available register and Excel data, but the message clearly said not complete and named the missing ERP file. It did not invent ERP figures. The export was restored and the normal run succeeded again.

## email (00:03:53,099)

The baseline controller summary was sent through Gmail and verified in the recipient's inbox. This view shows its actual message content with private email headers omitted. Rerunning the unchanged result skipped delivery, preventing a duplicate message. Email is an allowed assessment channel; the README explains the planned migration to WhatsApp.

## finish (00:04:09,729)

The full test suite passed twenty seven tests, including the review screen, retries and message delivery behavior. Coverage was ninety two percent, and lint, formatting and type checks passed. Further work includes stricter validation of human decisions and independent detection of extraction omissions. This demonstrates the working sample flow while keeping those limits explicit.
