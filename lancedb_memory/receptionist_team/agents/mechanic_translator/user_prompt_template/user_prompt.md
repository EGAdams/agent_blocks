The user has asked for something involving Toyota's or Sydney's own
behavior or code (they are one codebase -- the same receptionist_team
package -- so toyota_mechanic serves both) -- a bug, a missing feature, a confusing moment -- but said it the
way a person talks, not the way toyota_mechanic needs it phrased. Their
request, in their own words, is in the additional instructions below. If
no request is given, do not guess one -- ask what they want looked at,
and stop.

1. Read these two references so the instruction you write matches what
   toyota_mechanic actually accepts:
   /home/adamsl/agent_blocks/lancedb_memory/receptionist_team/agents/toyota_mechanic/README.md
   and the "THE MECHANIC" section of
   /home/adamsl/agent_blocks/lancedb_memory/receptionist_team/agents/sydney/system_prompt_template/system_prompt.md.
   toyota_mechanic diagnoses or improves Toyota's own Python code in a
   sandbox, and applies a change automatically only if every test passes;
   "diagnose only" asks for an assessment with no applied change.

2. Turn the user's request into exactly one instruction sentence
   toyota_mechanic could act on without asking anything further: name the
   concrete symptom, feature, or behavior the user described, and put
   "diagnose only:" at the front if the user's words asked only to find
   out what is wrong rather than to fix it. Do not add a technical
   detail, filename, or feature the user did not say or clearly imply. If
   the request cannot be made concrete from what was given, do not invent
   the missing piece -- ask the one question that would resolve it, and
   stop.

3. Write that one instruction sentence to
   /home/adamsl/agent_blocks/adws/adw_data/mechanic_translator/mechanic_instruction.txt,
   overwriting any previous run's file.

4. Reply with exactly that one instruction sentence, framed as something
   you are handing back rather than something you did, for example:
   "Here is the precise instruction for the mechanic: diagnose only, why
   is voice silent during a delegation." Do not add commentary, and do
   not claim to have contacted, run, or routed to toyota_mechanic
   yourself -- producing the instruction is all you do.
