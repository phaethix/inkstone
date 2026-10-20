{{source_chunk}}

Known character assets:
{{characters_json}}

{{current_bible_block}}Reconcile character identities for the visual bible.
- Prefer merging pronouns, kinship labels, honorifics, surnames, initials, and short descriptive tags for the same person (e.g. 'the man (as narrated)', 'he (the beloved)', 'Mr. Li', 'R·') into one canonical.
- Age variants → stages under one canonical, not new root characters.
- Use confidence=high only when clearly the same person; otherwise low.
- Never invent English prose character names (no 'man with dark hair, wearing a jacket').
- Never high-merge incompatible roles (mother≠daughter, count≠novelist, servant≠master).
- Never high-merge conflicting genders (male≠female) or letter_reader↔letter_writer.
- Fill project era once (e.g. 'Vienna c.1900–1910' or 'contemporary China') and keep it stable across chunks; set era_forbidden_wardrobe when helpful.
- Every canon must include gender (male/female/nonbinary/unknown) and narrative_function (letter_reader/letter_writer/protagonist/love_interest/servant/parent/child/extra).
- Always fill face_lock, hair_lock, outfit_lock, and age_look for every stage.
- For historical eras use period wardrobe only — never hoodies, sneakers, or athleisure.
- portrait_key must be short form {canonical_name}@{stage} only (e.g. R@adult), never prose.
- {{bible_note}}
String alias hints:
{{alias_hints}}
