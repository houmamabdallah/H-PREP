# Resolve prep menu

Install the menu scripts with `bash prep/install.sh` from the repository root,
after filling in the [configuration](../README.md). Open a timeline and choose
a command from **Workspace → Scripts**.

| Command | Result |
|---|---|
| Group Shots (preview) | Sample three frames per shot and write suggested groups and a contact sheet under `state_dir`. Review before applying. |
| Group Shots (make GROUPED timeline) | Duplicate the timeline and assign similar shots to colour groups on the copy. |
| Save Tree Template | Park on a graded shot to save its node tree in `templates_dir`. |
| Apply Tree Template | Copy the saved tree onto empty shots on a duplicated prep timeline. |
| Spread Group Grades | Copy a graded shot's tree to empty members of its group on a duplicate. Park on a graded shot to choose it when the group has several. |
| Set Group Look | Choose a DCTL from `looks_dir` for each group's post-clip node 1 on a duplicate. |
| Mattes (attach) | Attach existing files in `mattes_dir` to their source clips. Attachments follow the media pool clip across timelines. |
| Status | Report groups, grades, looks, clip mattes and saved templates. |

Apply Tree Template, Spread Group Grades and Set Group Look require a timeline
with GROUPED or PREP in its name. They create dated copies with their own
groups. Group pre-clip adjustments affect every member. Clip nodes hold each
shot's trims, masks and tracking. Group post-clip holds the shared look.

For a source named `PROJECT_A.mov`, the matte tool first looks for
`PROJECT_A_MATTES.mov` and numbered selections such as
`PROJECT_A_P1_MATTES.mov`. It also accepts separate person and skin files if
there is no packed file. Existing attachments are skipped.

Run the same commands from a terminal with `python3 prep/prep_tree.py`, followed
by `status`, `group`, `group apply`, `template save NAME`, `template apply NAME`,
`spread`, `look NAME` or `attach`.
