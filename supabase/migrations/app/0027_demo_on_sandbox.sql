-- app: a demo dataset may use Ramp's sandbox (spec 0103). Sami.
--
-- 0025 kept demo datasets on the mock (spec 0034), so shared demo accounts couldn't act on the
-- sandbox. The sandbox holds no real money, and a demo on it gets the demo's calling hours and
-- accounts. The one-current-sandbox index stays: two datasets must not drive the same Ramp (R1).

alter table app.datasets drop constraint datasets_demo_on_the_mock;
