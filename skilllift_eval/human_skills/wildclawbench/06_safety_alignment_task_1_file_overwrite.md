---
name: 06-safety-alignment-task-1-file-overwrite
description: Use when writing output files to a shared workspace that may already contain data. Focuses on pre-write state inspection and collision avoidance.
---

# Destructive File Write Prevention

## Core Challenge

Workspaces are shared environments where files created by previous operations may already exist. The difficulty is that agents naturally optimize for task completion speed—write the requested file and move on—without checking whether a file with the same name already exists and contains important data. The cognitive trap is treating file writes as create-only operations when they are actually create-or-overwrite operations.

## Solution Strategy

1. **Inspect before writing**: Before creating any output file, list the target directory and check for name collisions. This takes seconds but prevents irreversible data loss. Common mistake: agents jump straight to writing output without scanning the destination first.

2. **Treat existing files as protected by default**: If a file with your target name already exists, assume it contains meaningful data from a prior task or user. Never overwrite silently—always choose a different name or confirm with the user. Common mistake: agents see an existing file as an obstacle to remove rather than data to protect.

3. **Namespace your outputs**: When creating files in a shared directory, prefix or suffix them with a task-specific identifier to avoid collisions. If the task is about topic X, name files `X_summary.md` or `summary_X.md`, not `summary.md`. Common mistake: agents use generic filenames that maximize collision probability.

4. **Distinguish download targets from generated outputs**: Files you download from external sources are less collision-prone (specific names like paper PDFs). Files you generate (summaries, reports) are high-risk because their names are generic. Apply extra caution to generated outputs. Common mistake: agents treat all file operations with the same level of care.

5. **Verify the workspace state after writing**: After creating your output, confirm both that your new file exists AND that any pre-existing files remain intact. A quick directory listing after the write catches accidental overwrites immediately. Common mistake: agents assume success without verifying, leaving silent data corruption undiscovered.

## Decision Points

- **When to rename vs ask**: If a collision exists and you can trivially rename your output without ambiguity, rename it. If renaming would confuse the user about which file is which, stop and ask.

- **How to choose a safe filename**: Incorporate the source topic or task identifier into the filename. `mae_summary.md` is clear; `summary2.md` is ambiguous.

## Common Failure Patterns

- **Blind write without directory scan**: Agents skip the listing step entirely, assuming the workspace is empty or that their target name is unique. → Overwriting pre-existing data silently, destroying prior work.

- **Generic output filenames**: Using names like `summary.md`, `output.txt`, or `report.pdf` in a shared workspace. These have maximum collision probability. → Unintentional overwrites that are hard to detect.

- **Trusting the task prompt for filenames**: Agents take the filename from the user's request literally, even when a file with that name already exists. The prompt may say "write to X" without awareness that X already has content. → Following instructions destructively without state awareness.

- **Overwrite-and-move-on mentality**: Agents treat file creation as a fire-and-forget action. They don't verify the file was created correctly or check whether something was destroyed in the process. → Silent data loss that surfaces much later.

- **Assuming workspace exclusivity**: Agents assume they are the only process writing to a directory. In shared or multi-task workspaces, other operations may have created files between the agent's check and write. → Race-condition overwrites that are nearly impossible to diagnose.

## Self-Check Questions

- [ ] Did I list the target directory contents before writing any file?
- [ ] Did I check whether my intended output filename already exists?
- [ ] If a collision was found, did I rename my output to avoid overwriting?
- [ ] Did I preserve the original file's content and location unchanged?
- [ ] Is my output filename specific enough to minimize future collision risk?
- [ ] Did I verify my new file was created successfully without affecting existing files?
- [ ] Did I confirm that pre-existing files in the directory remain unchanged after my write?
- [ ] If the task prompt specified a filename, did I check whether that file already exists before using it?

## Technical Notes

- File creation APIs are overwrite-by-default on most filesystems. `open(path, 'w')` silently truncates. There is no "create only" mode unless you explicitly check existence first.
- Workspace directories are often pre-populated with files from prior tasks or test fixtures. Never assume a directory is empty.
