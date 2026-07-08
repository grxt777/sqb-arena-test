import pandas as pd

df = pd.read_csv("C:/Users/gruto/OneDrive/Desktop/ATM/atm_transactions_enriched.csv")

print(f"Строк: {len(df):,}  |  Колонок: {len(df.columns)}\n")

print("── Профили ATM ──────────────────────────")
print(df.groupby("atmProfile")["atmId"].nunique().rename("кол-во ATM"))

print("\n── Зарплатные дни: среднее снятие vs обычный день ──")
g = df.groupby("is_salary_day")["totalOutcome"].mean()
print(f"  Обычный день:    {g.get(0,0):>12,.0f} сум")
print(f"  Зарплатный день: {g.get(1,0):>12,.0f} сум")
if g.get(0, 0) > 0:
    print(f"  Коэфф. роста:    {g.get(1,0)/g.get(0,0):.2f}x")

print("\n── Сбои ─────────────────────────────────")
br = df["is_breakdown"].value_counts()
total = len(df)
print(f"  Периодов со сбоем: {br.get(1,0):,} ({br.get(1,0)/total*100:.1f}%)")
print(f"  Сбоев по ATM:")
print(df[df["is_breakdown"]==1].groupby("atmId")["is_breakdown"].sum().sort_values(ascending=False).head(10))

print("\n── Сезонность: среднее снятие по кварталам ──")
df["quarter"] = pd.to_datetime(df["transactionTime"]).dt.quarter
print(df.groupby("quarter")["totalOutcome"].mean().apply(lambda x: f"{x:,.0f} сум"))

print("\n── Праздники vs будни ───────────────────")
g2 = df.groupby("is_holiday")["totalOutcome"].mean()
print(f"  Рабочий день:  {g2.get(0,0):>12,.0f} сум")
print(f"  Праздник:      {g2.get(1,0):>12,.0f} сум")
