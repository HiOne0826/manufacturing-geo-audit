import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function AnalyticsChart({ rows }: { rows: Array<Record<string, string | number>> }) {
  return <ResponsiveContainer width="100%" height={280}><BarChart data={rows}><XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} height={72} angle={-18} textAnchor="end" /><YAxis /><Tooltip /><Bar dataKey="命中率" fill="#2868d8" /><Bar dataKey="Top3" fill="#18a77b" /><Bar dataKey="官网引用率" fill="#b65b12" /></BarChart></ResponsiveContainer>;
}
