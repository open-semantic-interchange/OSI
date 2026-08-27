-- ============================================================================
-- Tableau BI SQL examples: LoDs for two-stage aggregation
--
-- Measures compose with Tableau's own FIXED level-of-detail (LoD) calculations.
-- This computes a measure at a per-state grain, then averages that result: for
-- each category, the average across its states of total revenue.
--
-- The generated SQL is two-stage: an inner query computes the per-state measure
-- (MEASURE(total_revenue) grouped by state_id), and the outer query applies the
-- second aggregation (AVG) after joining on the state grain. The join uses
-- IS NOT DISTINCT FROM so NULL state_id values match.
--
-- Tableau feature: FIXED Level of Detail Expressions.
-- https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod_fixed.htm
-- ============================================================================

SELECT
  `t0`.`category` AS `category`,
  AVG(`t1`.`x_measure__1`) AS `average_of_total_revenues_by_state`
FROM
  (
    SELECT
      `orders_metrics`.`category` AS `category`,
      `orders_metrics`.`state_id` AS `state_id`
    FROM
      `orders_metrics` `orders_metrics`
    GROUP BY
      1,
      2
  ) `t0`
    JOIN (
      SELECT
        `orders_metrics`.`state_id` AS `state_id`,
        (MEASURE(`orders_metrics`.`total_revenue`)) AS `x_measure__1`
      FROM
        `orders_metrics` `orders_metrics`
      GROUP BY
        1
    ) `t1`
      ON (`t0`.`state_id` IS NOT DISTINCT FROM `t1`.`state_id`)
GROUP BY
  1;
