import { RuleGeneratorWizard } from "@/components/rules/RuleGeneratorWizard";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";

export default function GenerateRulesPage() {
  return (
    <div className="mx-auto max-w-3xl px-10 py-10">
      <Masthead
        edition="Library · §04 / Generate"
        title={<>Extract rules <span className="italic">from regulator&rsquo;s text</span></>}
        subtitle="Upload an IRDAI / SEBI circular or paste its text. The AI extracts compliance rules; you review, edit, and bulk-accept into the library."
        meta={
          <>
            <MetaItem label="Steps" value="2" />
            <MetaItem label="Workflow" value="Upload → Review" />
          </>
        }
      />
      <RuleGeneratorWizard />
    </div>
  );
}
