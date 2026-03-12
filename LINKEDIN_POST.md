# LinkedIn Post — AKS AI Monitoring Agent

---

🤖 I built an AI agent that monitors and heals my Kubernetes cluster — and I open-sourced it.

Here's the problem I was solving:

My AKS cluster would break at 2am. A pod crashes. A node goes NotReady. A PVC gets stuck. By the time I wake up, there are 47 alerts in my inbox and a scramble to figure out what happened and why.

So I built something that handles all of that while I sleep.

---

**What the AKS AI Monitoring Agent does:**

🔍 **Monitors** — Checks your AKS cluster every 5 minutes. Nodes, pods, deployments, PVCs, Azure node pools, cert-manager certificates, everything.

🧠 **Diagnoses with AI** — When something breaks, it sends the cluster state to Claude (Anthropic's AI) and gets back a structured diagnosis: Is this a configuration problem (something you did) or an Azure platform problem (something Microsoft did)?

🔧 **Auto-fixes what it can** — CrashLoopBackOff? Deletes the pod so K8s recreates it. OOMKilled? Patches the deployment to increase memory limits. NotReady node? Cordons and drains it safely. All verified after execution.

📧 **Emails you when it needs help** — If the fix requires a human, you get an email with the exact steps and kubectl commands to run. Not "something broke" — "here's exactly what to do and why."

☁️ **Escalates Azure platform issues** — If it's a Microsoft problem, it automatically opens an Azure Support case AND a GitHub issue on Azure/AKS. You wake up and the ticket is already filed.

📋 **Documents everything** — Every issue, every fix, every escalation goes into a SQLite database with full audit trail and a generated Markdown report.

---

**The tech stack:**
- Python + Kubernetes client
- Claude AI (Anthropic) for intelligent diagnosis
- Azure SDK for node pool checks and support case creation
- PyGithub for issue creation
- GitHub Actions for CI/CD (build → test → push to GHCR → deploy to AKS)
- Runs inside your cluster as a K8s Deployment with proper RBAC

---

**What I learned building this:**

The hardest part wasn't the code. It was writing the AI system prompt that gets Claude to reliably distinguish between "you misconfigured your memory limits" and "Azure is having a bad day."

The second hardest part was making sure auto-fixes are SAFE. The agent never touches secrets, never scales to zero, and always verifies a fix actually worked before marking it resolved.

---

**It's fully open source.** Fork it, adapt it to your cluster, contribute back.

👉 GitHub: [link to repo]

If you run AKS and you're tired of being paged for problems that have obvious fixes — this is for you.

#Kubernetes #AKS #Azure #AI #DevOps #SRE #OpenSource #CloudNative #Automation #AnthropicAI

---

*(Drop a comment if you try it — would love to hear what problems it catches first on your cluster.)*
