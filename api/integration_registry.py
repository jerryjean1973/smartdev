"""Fixed provider endpoints and versioned setup guides."""
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


@dataclass(frozen=True)
class Provider:
    name: str
    category: str
    documentation: str
    account: str
    permission: str
    steps: tuple[str, ...]
    verification: str
    endpoint: str = ""
    capability: str = ""
    header: str = "Authorization"
    prefix: str = "Bearer "
    version: int = 1
    setup_url: str = ""


PROVIDERS = {
    "openai": Provider(
        "OpenAI API", "AI providers", "https://developers.openai.com/api/reference/python/resources/models/methods/list",
        "OpenAI API project", "Read access to the Models endpoint",
        ("Open the API keys page at platform.openai.com/api-keys and select the intended project.",
         "Create a project API key with model listing access, then paste it in this panel."),
        "Validate the key and check the returned model IDs. Codex CLI uses its existing authentication.",
        "https://api.openai.com/v1/models", "List available models", setup_url="https://platform.openai.com/api-keys"),
    "anthropic": Provider(
        "Anthropic Claude", "AI providers", "https://platform.claude.com/docs/en/manage-claude/authentication",
        "Claude Console workspace", "A workspace API key with access to list models",
        ("Open Claude Console and select the workspace where usage should be billed.",
         "Create an API key on the workspace API keys page and paste it in this panel."),
        "Validate the key and check the returned model IDs.",
        "https://api.anthropic.com/v1/models?limit=100", "List available models", "x-api-key", "",
        setup_url="https://platform.claude.com/settings/keys"),
    "gitlab": Provider(
        "GitLab.com", "Source control", "https://docs.gitlab.com/user/profile/personal_access_tokens/",
        "GitLab.com user", "read_user",
        ("Open the personal access token page from your GitLab user settings.",
         "Create a short-lived token with read_user and paste it in this panel."),
        "Validate the token and confirm the returned account name.",
        "https://gitlab.com/api/v4/user", "Read authenticated account", "PRIVATE-TOKEN", ""),
    "github": Provider(
        "GitHub", "Source control", "https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app",
        "GitHub user or organization with App installation permission", "Metadata read for the repository picker",
        ("Install the deployment's GitHub App on the repositories you intend to import.",
         "Configure the installation on the server and use the Coder authorization link for workspace Git access."),
        "Validate repository discovery and check that the intended repository appears."),
    "cloudflare": Provider(
        "Cloudflare", "Domains & DNS", "https://developers.cloudflare.com/fundamentals/api/get-started/create-token/",
        "Cloudflare account with control of the intended zone", "Zone Read scoped to the intended zone",
        ("Create a Cloudflare user API token limited to the intended zone with Zone Read.",
         "Paste the token and validate zone discovery."),
        "Check the listed zones. Zone discovery does not verify authoritative DNS or write access.",
        "https://api.cloudflare.com/client/v4/zones?per_page=50", "List readable zones"),
    "search-console": Provider(
        "Google Search Console", "Google & SEO", "https://search.google.com/search-console",
        "Google account with domain or property access", "DNS control for domain verification",
        ("Add a Domain property in Search Console and copy the TXT value Google provides.",
         "Publish the exact TXT record using the authoritative DNS provider.",
         "Return to Search Console and select Verify."),
        "Confirm ownership in Search Console. For DNS failures, check the authoritative zone and allow time for propagation."),
    "gemini": Provider(
        "Google Gemini", "AI providers", "https://ai.google.dev/gemini-api/docs/api-key",
        "Google AI Studio project", "Permission to create and use a Gemini API key",
        ("Select the intended project in Google AI Studio.", "Follow Google's API key guide for the project."),
        "Use the official guide's sample request and confirm it returns a model response."),
    "godaddy": Provider(
        "GoDaddy", "Domains & DNS", "https://developer.godaddy.com/en/docs/api-users",
        "GoDaddy account eligible for the Domains API", "Read access to the intended domain and DNS zone",
        ("Check that the domain uses GoDaddy as its authoritative DNS host.",
         "Check account eligibility and follow the official first-call guide with a read-only request."),
        "Compare the returned zone with the domain's authoritative nameservers."),
    "namecheap": Provider(
        "Namecheap", "Domains & DNS", "https://www.namecheap.com/support/api/intro/",
        "Namecheap account eligible for API access", "API access and an allowlisted source IPv4 address",
        ("Review the API eligibility requirements and enable access in the intended account.",
         "Allowlist the fixed address of the host that will make requests and test the sandbox first."),
        "Confirm read-only domain discovery before working with DNS."),
    "vultr": Provider(
        "Vultr DNS", "Domains & DNS", "https://docs.vultr.com/products/network/dns/provisioning",
        "Vultr account with access to the intended DNS zone", "Permission to list DNS domains and records",
        ("Identify the zone in the Vultr DNS console.",
         "Use the read requests in Vultr's API guide to list the zone and its records."),
        "Compare the returned zone and records with the public delegation before planning DNS changes."),
    "vercel": Provider(
        "Vercel", "Cloud & hosting", "https://vercel.com/docs/rest-api",
        "Vercel account or team with access to the intended project", "Read access to the intended project",
        ("Select the correct team and follow the REST API authentication guide.",
         "Use the documented project listing endpoint to confirm access."),
        "Check the intended project appears under the expected team."),
    "netlify": Provider(
        "Netlify", "Cloud & hosting", "https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/",
        "Netlify team member with project access", "Read access to the intended project",
        ("Use the official authentication instructions with the intended Netlify account.",
         "Make the guide's read-only request before configuring deployment."),
        "Check the returned resources against the expected team in Netlify."),
    "google-analytics": Provider(
        "Google Analytics 4", "Google & SEO", "https://developers.google.com/analytics/devguides/config/admin/v1",
        "Google account with Analytics property access", "Read access to the intended property and stream",
        ("Find the existing property and web data stream in Analytics Admin.",
         "Inspect current tracking and consent before making changes to the Measurement ID or tag."),
        "Use Analytics Realtime or DebugView to check receipt of a test event."),
    "google-ads": Provider(
        "Google Ads", "Google & SEO", "https://developers.google.com/google-ads/api/docs/api-policy/developer-token",
        "Google Ads manager account with access to the target customer", "Developer token with an appropriate access level and OAuth authorization",
        ("Find API Center in the manager account and follow the developer-token application guide.",
         "Start on a test account and verify read access before considering campaign changes."),
        "Confirm the developer-token access level and retrieve the expected test customer."),
    "google-business": Provider(
        "Google Business Profile", "Google & SEO", "https://developers.google.com/my-business/content/prereqs",
        "Eligible Business Profile manager and Google Cloud project", "Business Profile API access approval",
        ("Review the official eligibility requirements and request access for the intended Cloud project.",
         "After approval, confirm the Google account can administer the intended profile."),
        "Verify the approved project can list the expected Business Profile accounts."),
    "meta-pages": Provider(
        "Facebook Pages", "Meta", "https://developers.facebook.com/docs/graph-api/reference/user/accounts/",
        "Meta app and an account with access to the intended Page", "Page access and pages_show_list for discovery",
        ("In the Meta App Dashboard, confirm the app and the account that administers the test Page.",
         "Follow Meta's access-token instructions and inspect the Pages available to that account."),
        "Check that the Page ID returned by discovery matches the intended test Page."),
    "meta-instagram": Provider(
        "Instagram", "Meta", "https://developers.facebook.com/docs/instagram/platform/instagram-api/changelog/",
        "Instagram Business or Creator account and a Meta app", "instagram_business_basic for Instagram Login account reads",
        ("Add the Instagram product to the intended Meta app.",
         "Authorize the test professional account using Instagram Login."),
        "Use a read-only Instagram profile request and match the account ID to the test account."),
    "meta-ads": Provider(
        "Meta Ads", "Meta", "https://developers.facebook.com/documentation/ads-commerce/marketing-api",
        "Meta app and access to the intended ad account", "ads_read for reporting",
        ("Confirm the test ad account in Ads Manager and the app in the Meta App Dashboard.",
         "Follow the Marketing API guide to authorize reporting access for that account."),
        "Retrieve a report from the expected test ad account and confirm the account ID before enabling any campaign changes."),
    "meta-conversions": Provider(
        "Meta Pixel & Conversions API", "Meta", "https://developers.facebook.com/documentation/ads-commerce/conversions-api",
        "Meta business with access to the intended dataset", "Permission to configure the dataset in Events Manager",
        ("Identify the existing dataset in Meta Events Manager and review existing browser and server tracking.",
         "Follow the official setup guide and use matching event IDs to deduplicate paired events."),
        "Use Test Events in Events Manager and check event receipt and deduplication before enabling production reporting."),
}


@dataclass(frozen=True)
class Probe:
    status: str
    message: str
    items: tuple[str, ...] = ()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def probe(provider_id, credential):
    provider = PROVIDERS[provider_id]
    headers = {provider.header: provider.prefix + credential, "Accept": "application/json"}
    if provider_id == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    request = Request(provider.endpoint, headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=8) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        if provider_id == "gitlab":
            values = [payload["username"]]
        elif provider_id == "cloudflare":
            if payload.get("success") is not True:
                raise ValueError
            values = [item["name"] for item in payload["result"]]
        else:
            values = [item["id"] for item in payload["data"]]
        items = tuple(value for value in values[:100] if isinstance(value, str) and 0 < len(value) <= 200
                      and credential not in value and not any(ord(char) < 32 for char in value))
        return Probe("OPERATIONAL", provider.capability + " verified.", items)
    except HTTPError as error:
        try:
            if error.code == 401:
                return Probe("AUTHORIZATION_REQUIRED", "Credential was rejected. Check or rotate it with the provider.")
            if error.code == 403:
                return Probe("CONFIGURATION_REQUIRED", "Provider denied access. Check the required permission and account policy.")
            if error.code == 429 or error.code >= 500:
                return Probe("DEGRADED", "Provider is unavailable or rate-limited. Verify again later.")
            return Probe("FAILED", "Provider rejected the verification request.")
        finally:
            error.close()
    except (URLError, OSError):
        return Probe("DEGRADED", "Could not reach the provider. Verify again later.")
    except (TypeError, ValueError, KeyError):
        return Probe("FAILED", "Provider returned an unexpected verification response.")
