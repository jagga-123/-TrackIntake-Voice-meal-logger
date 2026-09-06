import PropTypes from 'prop-types';
import { useMemo, useState } from 'react';

import styles from './VoiceMealLogger.module.css';

export const MEAL_TYPES = [
  { value: 'breakfast', label: 'Breakfast' },
  { value: 'lunch', label: 'Lunch' },
  { value: 'dinner', label: 'Dinner' },
  { value: 'snack', label: 'Snack' },
];

export const UNITS = ['piece', 'bowl', 'cup', 'plate', 'g', 'ml', 'tbsp', 'tsp'];

const MACRO_COLUMNS = [
  { key: 'calories', label: 'kcal', step: 1 },
  { key: 'protein_g', label: 'Protein (g)', step: 0.1 },
  { key: 'carbs_g', label: 'Carbs (g)', step: 0.1 },
  { key: 'fats_g', label: 'Fat (g)', step: 0.1 },
];

const macrosShape = PropTypes.shape({
  calories: PropTypes.number.isRequired,
  protein_g: PropTypes.number.isRequired,
  carbs_g: PropTypes.number.isRequired,
  fats_g: PropTypes.number.isRequired,
});

export const previewShape = PropTypes.shape({
  transcript: PropTypes.string.isRequired,
  language: PropTypes.string,
  duration: PropTypes.number,
  meal_type: PropTypes.oneOf(MEAL_TYPES.map((type) => type.value)).isRequired,
  meal_type_source: PropTypes.oneOf(['transcript', 'time_of_day']),
  confidence: PropTypes.number.isRequired,
  total_macros: macrosShape.isRequired,
  items: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      original_text: PropTypes.string,
      quantity: PropTypes.number.isRequired,
      unit: PropTypes.oneOf(UNITS).isRequired,
      assumed_quantity: PropTypes.bool.isRequired,
      macro_source: PropTypes.oneOf(['table', 'llm_estimate']).isRequired,
      matched_food: PropTypes.string,
      confidence: PropTypes.number,
      macros: macrosShape.isRequired,
    }),
  ).isRequired,
});

let rowSequence = 0;
const nextRowId = () => `row-${(rowSequence += 1)}`;

const round = (value, places) => Math.round(Number(value) * 10 ** places) / 10 ** places;

/** Convert preview items into locally editable rows with stable ids. */
function toEditableRows(items) {
  return items.map((item) => ({ ...item, rowId: nextRowId(), macros: { ...item.macros } }));
}

function blankRow() {
  return {
    rowId: nextRowId(),
    name: '',
    original_text: '',
    quantity: 1,
    unit: 'piece',
    assumed_quantity: false,
    macro_source: 'llm_estimate',
    matched_food: null,
    confidence: 1,
    macros: { calories: 0, protein_g: 0, carbs_g: 0, fats_g: 0 },
  };
}

function sumMacros(rows) {
  return rows.reduce(
    (totals, row) => {
      MACRO_COLUMNS.forEach(({ key }) => {
        totals[key] += Number(row.macros[key]) || 0;
      });
      return totals;
    },
    { calories: 0, protein_g: 0, carbs_g: 0, fats_g: 0 },
  );
}

/** Return the first validation problem, or null when the rows can be saved. */
function validateRows(rows) {
  if (rows.length === 0) return 'Add at least one item before saving.';
  for (const row of rows) {
    if (!row.name.trim()) return 'Every item needs a name.';
    if (!(Number(row.quantity) > 0)) return `Quantity for “${row.name || 'item'}” must be greater than 0.`;
    if (MACRO_COLUMNS.some(({ key }) => Number(row.macros[key]) < 0 || Number.isNaN(Number(row.macros[key])))) {
      return `Macros for “${row.name}” must be zero or positive numbers.`;
    }
  }
  return null;
}

/** Shape a row into the confirm endpoint's item payload. */
function serialiseRow(row) {
  return {
    name: row.name.trim(),
    original_text: row.original_text || row.name.trim(),
    quantity: round(row.quantity, 2),
    unit: row.unit,
    assumed_quantity: row.assumed_quantity,
    macro_source: row.macro_source,
    confidence: row.confidence ?? 1,
    macros: Object.fromEntries(MACRO_COLUMNS.map(({ key }) => [key, round(row.macros[key], 1)])),
  };
}

/**
 * Editable review of a voice preview: transcript, per-item table with macro
 * inputs, live totals, provenance badges and confirm / re-record actions.
 */
export default function MealPreviewCard({
  preview,
  onConfirm,
  onRerecord,
  saving = false,
  error = null,
}) {
  const [rows, setRows] = useState(() => toEditableRows(preview.items));
  const [mealType, setMealType] = useState(preview.meal_type);

  const totals = useMemo(() => sumMacros(rows), [rows]);
  const validationMessage = useMemo(() => validateRows(rows), [rows]);

  const patchRow = (rowId, patch) =>
    setRows((current) => current.map((row) => (row.rowId === rowId ? { ...row, ...patch } : row)));

  const patchMacro = (rowId, key, value) =>
    setRows((current) =>
      current.map((row) =>
        row.rowId === rowId ? { ...row, macros: { ...row.macros, [key]: value } } : row,
      ),
    );

  /** Rescale macros proportionally when the quantity changes (they are per portion). */
  const changeQuantity = (row, rawValue) => {
    const previous = Number(row.quantity);
    const next = Number(rawValue);
    const patch = { quantity: rawValue, assumed_quantity: false };
    if (previous > 0 && next > 0) {
      const factor = next / previous;
      patch.macros = Object.fromEntries(
        MACRO_COLUMNS.map(({ key }) => [key, round(Number(row.macros[key]) * factor, 1)]),
      );
    }
    patchRow(row.rowId, patch);
  };

  const removeRow = (rowId) => setRows((current) => current.filter((row) => row.rowId !== rowId));
  const addRow = () => setRows((current) => [...current, blankRow()]);

  const handleSubmit = (event) => {
    event.preventDefault();
    if (validationMessage || saving) return;
    onConfirm({
      meal_type: mealType,
      transcript: preview.transcript,
      items: rows.map(serialiseRow),
    });
  };

  const confidencePct = Math.round(preview.confidence * 100);

  return (
    <form className={styles.preview} onSubmit={handleSubmit} noValidate>
      <header className={styles.previewHeader}>
        <div>
          <h2 className={styles.cardTitle}>Review your meal</h2>
          <p className={styles.cardSubtitle}>Edit anything that looks off, then confirm.</p>
        </div>
        <span
          className={`${styles.confidence} ${confidencePct < 70 ? styles.confidenceLow : ''}`}
          title="How confident the parser was in this extraction"
        >
          {confidencePct}% confident
        </span>
      </header>

      <blockquote className={styles.transcript}>
        <span className={styles.transcriptLabel}>You said</span>“{preview.transcript}”
        {preview.language && <span className={styles.transcriptMeta}>{preview.language.toUpperCase()}</span>}
      </blockquote>

      <label className={styles.mealTypeField}>
        <span>Meal</span>
        <select value={mealType} onChange={(event) => setMealType(event.target.value)}>
          {MEAL_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </select>
        {preview.meal_type_source === 'time_of_day' && (
          <small>Guessed from the time of day — change it if needed.</small>
        )}
      </label>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Item</th>
              <th>Qty</th>
              <th>Unit</th>
              {MACRO_COLUMNS.map(({ key, label }) => (
                <th key={key}>{label}</th>
              ))}
              <th>
                <span className={styles.srOnly}>Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.rowId}>
                <td data-label="Item">
                  <input
                    type="text"
                    value={row.name}
                    placeholder="Food name"
                    onChange={(event) => patchRow(row.rowId, { name: event.target.value })}
                    aria-label="Item name"
                  />
                  <div className={styles.badges}>
                    {row.assumed_quantity && (
                      <span className={`${styles.badge} ${styles.badgeWarning}`}>Assumed 1 serving</span>
                    )}
                    {row.macro_source === 'llm_estimate' ? (
                      <span className={`${styles.badge} ${styles.badgeIndigo}`}>AI estimate</span>
                    ) : (
                      <span className={`${styles.badge} ${styles.badgeSuccess}`}>
                        Table · {row.matched_food ?? row.name}
                      </span>
                    )}
                  </div>
                </td>
                <td data-label="Qty">
                  <input
                    type="number"
                    min="0"
                    step="0.25"
                    value={row.quantity}
                    onChange={(event) => changeQuantity(row, event.target.value)}
                    aria-label="Quantity"
                  />
                </td>
                <td data-label="Unit">
                  <select
                    value={row.unit}
                    onChange={(event) => patchRow(row.rowId, { unit: event.target.value })}
                    aria-label="Unit"
                  >
                    {UNITS.map((unit) => (
                      <option key={unit} value={unit}>
                        {unit}
                      </option>
                    ))}
                  </select>
                </td>
                {MACRO_COLUMNS.map(({ key, label, step }) => (
                  <td key={key} data-label={label}>
                    <input
                      type="number"
                      min="0"
                      step={step}
                      value={row.macros[key]}
                      onChange={(event) => patchMacro(row.rowId, key, event.target.value)}
                      aria-label={label}
                    />
                  </td>
                ))}
                <td data-label="" className={styles.removeCell}>
                  <button
                    type="button"
                    className={styles.removeButton}
                    onClick={() => removeRow(row.rowId)}
                    aria-label={`Remove ${row.name || 'item'}`}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className={styles.totalsRow}>
              <td data-label="Total">Total</td>
              <td data-label="" />
              <td data-label="" />
              {MACRO_COLUMNS.map(({ key, label }) => (
                <td key={key} data-label={label}>
                  {round(totals[key], key === 'calories' ? 0 : 1)}
                </td>
              ))}
              <td data-label="" />
            </tr>
          </tfoot>
        </table>
      </div>

      <div className={styles.tableActions}>
        <button type="button" className={styles.linkButton} onClick={addRow}>
          + Add an item
        </button>
        <span className={styles.hint}>Macros rescale automatically when you change a quantity.</span>
      </div>

      {(validationMessage || error) && (
        <p className={styles.errorText} role="alert">
          {error ?? validationMessage}
        </p>
      )}

      <div className={styles.actions}>
        <button type="button" className={styles.secondaryButton} onClick={onRerecord} disabled={saving}>
          Re-record
        </button>
        <button
          type="submit"
          className={styles.primaryButton}
          disabled={saving || Boolean(validationMessage)}
        >
          {saving ? 'Saving…' : 'Confirm & Save'}
        </button>
      </div>
    </form>
  );
}

MealPreviewCard.propTypes = {
  preview: previewShape.isRequired,
  onConfirm: PropTypes.func.isRequired,
  onRerecord: PropTypes.func.isRequired,
  saving: PropTypes.bool,
  error: PropTypes.string,
};
