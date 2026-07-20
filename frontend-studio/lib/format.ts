export const gradeFromScore = (n: number) =>
  n >= 90 ? "A" : n >= 80 ? "B" : n >= 70 ? "C" : n >= 60 ? "D" : "F";
export const formatScore = (n: number) => Math.round(n).toString();
export const formatPercent = (n: number) => `${Math.round(n)}%`;
export const formatCost = (usd: number) => `$${usd.toFixed(2)}`;
export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
