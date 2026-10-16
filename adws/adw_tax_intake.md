# Tax document intake

Source: [adw_tax_intake.py](adw_tax_intake.py), with supporting behavior from
[tax_intake.py](adw_modules/tax_intake.py) and
[document_localize.py](adw_modules/document_localize.py).

The workflow reads earlier notes about a scanned tax form, finds missing
information, asks you questions, and saves the results. It handles **one folder
per run by default** (`--limit 1`).

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant ADW as Tax intake workflow
    participant Log as ADW session log
    participant Files as Tax document folders
    participant Intake as Intake agent
    participant Explain as Explanation agent
    participant Show as Document helper
    participant Vision as Page location agent

    You->>ADW: Run adw_tax_intake.py with a request
    ADW->>ADW: Load config and validate the three agents
    ADW->>Log: Create or join session and record request
    ADW->>Files: Select folders
    Note over ADW,Files: --folder selects a named folder, even if reviewed before.<br/>Otherwise select folders without intake_notes.txt,<br/>up to --limit (default 1).
    Files-->>ADW: Selected folders

    alt No matching folders
        ADW->>Log: Record nothing_pending phase
        ADW->>Files: Rebuild INTAKE_STATUS.txt
        ADW->>Log: Finish session successfully
        ADW-->>You: No pending tax-document folders to survey
    else At least one selected folder
        loop Each selected folder
            ADW->>Log: Start survey_N phase
            ADW->>Files: Read existing JSON/Markdown extraction and concerns.md
            Files-->>ADW: Extracted text and list of scanned files
            Note over ADW,Intake: The intake agent receives existing text.<br/>It does not inspect the scanned page.

            ADW->>Log: Start extract_needs_N phase
            ADW->>Intake: What information is missing or uncertain?
            Intake-->>ADW: Form name, completeness, needed items,<br/>questions, and any recommendation to re-read

            ADW->>Log: Start review_N phase
            ADW-->>You: Show findings and numbered questions
            alt --auto-decision approve supplied
                ADW->>ADW: Approve without asking for human answers
            else Interactive review
                loop Each open question
                    loop Until you answer or leave it blank
                        ADW-->>You: Ask question and offer e to explain or s to show
                        alt You enter e
                            You->>ADW: Explain this question
                            ADW->>Explain: Explain using the existing extraction
                            Explain-->>ADW: Simple explanation and suggested next step
                            ADW-->>You: Show explanation and ask the same question again
                        else You enter s
                            You->>ADW: Show the document
                            alt No scanned file in this folder
                                ADW-->>You: No image file to show
                            else Scanned file available
                                ADW->>Show: Search first scanned file using OCR keywords
                                Show-->>ADW: Matching region, or no match
                                opt No keyword match
                                    ADW->>Vision: Inspect page and locate a relevant region
                                    Vision-->>ADW: Region coordinates, or no match
                                end
                                ADW->>Show: Draw red box on temporary copy if possible<br/>and open copy or original in Windows
                                Show-->>You: Display page, or instructions if opening fails
                            end
                            ADW-->>You: Ask the same question again
                        else You answer or press Enter
                            You->>ADW: Answer text, or blank to leave question open
                            ADW->>ADW: Keep answer in memory for this review
                        end
                    end
                end
                ADW-->>You: Mark this folder reviewed and move on? [y/N]
                You->>ADW: Yes or no
                ADW-->>You: Any other notes for this folder?
                You->>ADW: Notes, or blank
            end

            ADW->>Log: Record review decision and answer count
            ADW->>Log: Start write_notes_N phase
            ADW->>Files: Append timestamped findings, answers,<br/>and status to intake_notes.txt
            Note over ADW,Files: Status is complete only when the intake agent<br/>said complete AND the review was approved.<br/>Otherwise status is needs_info.<br/>Notes are written in both cases.
        end

        ADW->>Log: Start update_status phase
        ADW->>Files: Rebuild INTAKE_STATUS.txt from notes-file existence
        opt --message-out supplied
            ADW->>Files: Write summary to requested output path
        end
        ADW->>Log: Finish session successfully
        ADW-->>You: Folder statuses and summary
    end
```

**“Done” in `INTAKE_STATUS.txt` means the folder has `intake_notes.txt`.**
Folders with unanswered questions or `needs_info` are also removed from the
automatic pending list once notes exist. Use `--folder <name>` to revisit one.
A successful ADW session means the intake pass finished; tax questions may still
need answers. This workflow records information and does not decide tax treatment.
