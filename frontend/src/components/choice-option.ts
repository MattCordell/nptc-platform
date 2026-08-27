/**
 * One option in a group of choices - shared by `CheckboxGroup` and
 * `RadioGroup` (issue #210) so the two groups cannot drift into taking
 * subtly different option shapes for the same job.
 *
 * `label` is the visible text and, because each option carries its own
 * `<label>`, also its accessible name; `value` is what the group reports
 * through `onChange`.
 */
export type ChoiceOption = {
  value: string;
  label: string;
  disabled?: boolean;
};
