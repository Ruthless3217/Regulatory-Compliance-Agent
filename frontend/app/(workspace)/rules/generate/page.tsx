import { RuleGeneratorWizard } from "@/components/rules/RuleGeneratorWizard";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";

export default function GenerateRulesPage() {
 return (
 <div className="mx-auto max-w-3xl px-8 py-8">
 <PageHeader
 title="Generate rules"
 description="Upload an IRDAI / SEBI circular or paste its text. The AI extracts compliance rules; you review, edit, and bulk-accept into the library."
 meta={
 <>
 <PageHeaderMeta label="Steps" value="2" />
 <PageHeaderMeta label="Workflow" value="Upload → Review" />
 </>
 }
 />
 <RuleGeneratorWizard />
 </div>
 );
}
