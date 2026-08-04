import { ReportTab } from "@/components/report/ReportTab";

export default function ReportPage() {
  // The submission layout no longer pads its tabs (the review workspace runs
  // edge to edge); the report is a document on the canvas and keeps its margin.
  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <ReportTab />
    </div>
  );
}
