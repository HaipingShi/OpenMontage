Score the supplied candidate against the shot description, official identity
references (each labeled artist separately), and supplied locked traits.
The candidate has already passed physical review. Perform only this scoring phase.

Rubric: 1 = strong contradiction; 2 = major mismatch; 3 = acceptable with visible
issues; 4 = convincing with minor issues; 5 = exceptionally precise.
Give one identity score per artist key in the attached references. If there are
no artists, return an empty identity object. State uncertainty for occluded faces
or traits; do not claim you verified an invisible wrist or unseen anatomy.
For lip-sync/performance shots, score whether visible jaw, vowels, breath and
articulation read as singing. This is a visual singing score only. The video's
native audio is replaced by the locked master; do not use native audio to judge
phoneme timing or claim synchronization to a master that was not supplied.

Return ONE flat JSON object with numeric scores from 1 through 5:
{"script_lyrics": 4, "identity": {"artist-id": 4}, "locked_traits": 4, "reasons": ["Concrete observations and evidence limitations"]}
Replace artist-id with each actual artist key. Include an additional numeric
"singing" field only when shot.render_route is performance_lipsync or
shot.generation_context.lip_sync is true. Omit it otherwise.
Do not wrap the object in a scoring/verification/result key. Do not provide
physical verdicts, markdown, or operational instructions. Do not infer lyrics
or music that are absent from the supplied evidence.
