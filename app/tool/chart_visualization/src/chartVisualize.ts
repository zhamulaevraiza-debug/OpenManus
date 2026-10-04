import "./nodeEnv";
import path from "path";
import fs from "fs";
import { randomBytes } from "crypto";
import puppeteer from "puppeteer";
import VMind, { ChartType, DataTable } from "@visactor/vmind";
import { isString } from "@visactor/vutils";

enum AlgorithmType {
  OverallTrending = "overallTrend",
  AbnormalTrend = "abnormalTrend",
  PearsonCorrelation = "pearsonCorrelation",
  SpearmanCorrelation = "spearmanCorrelation",
  ExtremeValue = "extremeValue",
  MajorityValue = "majorityValue",
  StatisticsAbnormal = "statisticsAbnormal",
  StatisticsBase = "statisticsBase",
  DbscanOutlier = "dbscanOutlier",
  LOFOutlier = "lofOutlier",
  TurningPoint = "turningPoint",
  PageHinkley = "pageHinkley",
  DifferenceOutlier = "differenceOutlier",
  Volatility = "volatility",
}

/** Prefix of the single stdout line carrying the JSON result for the Python side. */
const RESULT_MARKER = "__VMIND_RESULT__";

/** Local VChart bundle (used for offline PNG rendering) and its pinned CDN URL. */
const resolveVChartBundle = (): { path?: string; url: string } => {
  let version = "latest";
  try {
    version = require("@visactor/vchart/package.json").version;
  } catch {}
  const url = `https://unpkg.com/@visactor/vchart@${version}/build/index.min.js`;
  try {
    return { path: require.resolve("@visactor/vchart/build/index.min.js"), url };
  } catch {
    return { url };
  }
};

const escapeHtml = (text: string) =>
  text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

/** JSON that is safe to embed in an HTML <script> element. */
const toScriptSafeJson = (value: string) =>
  value
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");

/**
 * Serialize a chart spec. Functions (e.g. formatters produced by VMind) are encoded
 * as strings prefixed with a random per-document marker, so data values can never be
 * mistaken for code when the page revives them.
 */
const serializeSpec = (spec: any, functionMarker: string) => {
  return JSON.stringify(spec, (key, value) => {
    if (typeof value === "function") {
      const funcStr = value
        .toString()
        .replace(/(\r\n|\n|\r)/gm, "")
        .replace(/\s+/g, " ");

      return `${functionMarker}${funcStr}`;
    }
    return value;
  });
};

export function getHtmlVChart(
  spec: any,
  width?: number,
  height?: number,
  options: { includeLibrary?: boolean } = {}
) {
  const { includeLibrary = true } = options;
  const functionMarker = `__FUNCTION_${randomBytes(8).toString("hex")}__`;
  const title = escapeHtml(String(spec?.title?.text ?? "Chart"));
  const libraryTag = includeLibrary
    ? `<script src="${resolveVChartBundle().url}"></script>`
    : "";
  return `<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>${title}</title>
    ${libraryTag}
</head>
<body>
    <div id="chart-container" style="width: ${
      width ? `${Number(width)}px` : "100%"
    }; height: ${height ? `${Number(height)}px` : "100%"};"></div>
    <script type="application/json" id="chart-spec">${toScriptSafeJson(
      serializeSpec(spec, functionMarker)
    )}</script>
    <script>
      function renderChart() {
        var marker = ${JSON.stringify(functionMarker)};
        var source = document.getElementById("chart-spec").textContent;
        var spec = JSON.parse(source, function (k, v) {
          if (typeof v === "string" && v.indexOf(marker) === 0) {
            try {
              return new Function("return (" + v.slice(marker.length) + ")")();
            } catch (e) {
              console.error("Failed to parse function:", e);
              return function () {};
            }
          }
          return v;
        });
        var chart = new VChart.VChart(spec, { dom: "chart-container" });
        chart.renderSync();
      }
      if (window.VChart) {
        renderChart();
      }
    </script>
</body>
</html>
`;
}

/** Render a spec to PNG with headless Chromium (honours PUPPETEER_EXECUTABLE_PATH). */
export const getBase64 = async (spec: any, width?: number, height?: number) => {
  spec.animation = false;
  width && (spec.width = width);
  height && (spec.height = height);
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    const bundle = resolveVChartBundle();
    if (bundle.path) {
      await page.setContent(
        getHtmlVChart(spec, width, height, { includeLibrary: false })
      );
      await page.addScriptTag({ path: bundle.path });
      await page.evaluate("renderChart()");
    } else {
      await page.setContent(getHtmlVChart(spec, width, height), {
        waitUntil: "networkidle0",
      });
    }

    const dataUrl = await page.evaluate(() => {
      const canvas: any = document
        .getElementById("chart-container")
        ?.querySelector("canvas");
      return canvas?.toDataURL("image/png");
    });
    if (!dataUrl) {
      throw new Error("Chart rendering produced no canvas");
    }
    const base64Data = dataUrl.replace(/^data:image\/png;base64,/, "");
    return Buffer.from(base64Data, "base64");
  } finally {
    await browser.close();
  }
};

const OUTPUT_EXTENSIONS = ["json", "html", "png", "md"] as const;

/** A file name not used by any existing output of a previous chart. */
function getUniqueBaseName(directory: string, fileName: string) {
  let candidate = fileName;
  while (
    OUTPUT_EXTENSIONS.some((ext) =>
      fs.existsSync(path.join(directory, "visualization", `${candidate}.${ext}`))
    )
  ) {
    candidate += "_new";
  }
  return candidate;
}

function getSavedPathName(
  directory: string,
  fileName: string,
  outputType: "html" | "png" | "json" | "md"
) {
  return path.join(directory, "visualization", `${fileName}.${outputType}`);
}

const readStdin = (): Promise<string> => {
  return new Promise((resolve) => {
    let input = "";
    process.stdin.setEncoding("utf-8"); // 确保编码与 Python 端一致
    process.stdin.on("data", (chunk) => (input += chunk));
    process.stdin.on("end", () => resolve(input));
  });
};

/** Save insights markdown in local, and return content && path */
const setInsightTemplate = (
  path: string,
  title: string,
  insights: string[]
) => {
  let res = "";
  if (insights.length) {
    res += `## ${title} Insights`;
    insights.forEach((insight, index) => {
      res += `\n${index + 1}. ${insight}`;
    });
  }
  if (res) {
    fs.writeFileSync(path, res, "utf-8");
    return { insight_path: path, insight_md: res };
  }
  return {};
};

/** Save vmind result into local file, Return chart file path */
async function saveChartRes(options: {
  spec: any;
  directory: string;
  outputType: "png" | "html";
  fileName: string;
  width?: number;
  height?: number;
}) {
  const { directory, fileName, spec, outputType, width, height } = options;
  const specPath = getSavedPathName(directory, fileName, "json");
  fs.writeFileSync(specPath, JSON.stringify(spec, null, 2));
  const savedPath = getSavedPathName(directory, fileName, outputType);
  if (outputType === "png") {
    const base64 = await getBase64(spec, width, height);
    fs.writeFileSync(savedPath, base64);
  } else {
    const html = getHtmlVChart(spec, width, height);
    fs.writeFileSync(savedPath, html, "utf-8");
  }
  return savedPath;
}

async function generateChart(
  vmind: VMind,
  options: {
    dataset: string | DataTable;
    userPrompt: string;
    directory: string;
    outputType: "png" | "html";
    fileName: string;
    width?: number;
    height?: number;
    language?: "en" | "zh";
  }
) {
  let res: {
    chart_path?: string;
    error?: string;
    insight_path?: string;
    insight_md?: string;
  } = {};
  const {
    dataset,
    userPrompt,
    directory,
    width,
    height,
    outputType,
    fileName,
    language,
  } = options;
  try {
    // Get chart spec and save in local file
    const jsonDataset = isString(dataset) ? JSON.parse(dataset) : dataset;
    const { spec, error, chartType } = await vmind.generateChart(
      userPrompt,
      undefined,
      jsonDataset,
      {
        enableDataQuery: false,
        theme: "light",
      }
    );
    if (error || !spec) {
      return {
        error: error || "Spec of Chart was Empty!",
      };
    }

    spec.title = {
      text: userPrompt,
    };
    fs.mkdirSync(path.join(directory, "visualization"), { recursive: true });
    const baseName = getUniqueBaseName(directory, fileName);
    const specPath = getSavedPathName(directory, baseName, "json");
    res.chart_path = await saveChartRes({
      directory,
      spec,
      width,
      height,
      fileName: baseName,
      outputType,
    });

    // get chart insights and save in local
    const insights = [];
    if (
      chartType &&
      [
        ChartType.BarChart,
        ChartType.LineChart,
        ChartType.AreaChart,
        ChartType.ScatterPlot,
        ChartType.DualAxisChart,
      ].includes(chartType)
    ) {
      const { insights: vmindInsights } = await vmind.getInsights(spec, {
        maxNum: 6,
        algorithms: [
          AlgorithmType.OverallTrending,
          AlgorithmType.AbnormalTrend,
          AlgorithmType.PearsonCorrelation,
          AlgorithmType.SpearmanCorrelation,
          AlgorithmType.StatisticsAbnormal,
          AlgorithmType.LOFOutlier,
          AlgorithmType.DbscanOutlier,
          AlgorithmType.MajorityValue,
          AlgorithmType.PageHinkley,
          AlgorithmType.TurningPoint,
          AlgorithmType.StatisticsBase,
          AlgorithmType.Volatility,
        ],
        usePolish: false,
        language: language === "en" ? "english" : "chinese",
      });
      insights.push(...vmindInsights);
    }
    const insightsText = insights
      .map((insight) => insight.textContent?.plainText)
      .filter((insight) => !!insight) as string[];
    spec.insights = insights;
    fs.writeFileSync(specPath, JSON.stringify(spec, null, 2));
    res = {
      ...res,
      ...setInsightTemplate(
        getSavedPathName(directory, baseName, "md"),
        userPrompt,
        insightsText
      ),
    };
  } catch (error: any) {
    res.error = error.toString();
  } finally {
    return res;
  }
}

async function updateChartWithInsight(
  vmind: VMind,
  options: {
    directory: string;
    outputType: "png" | "html";
    fileName: string;
    insightsId: number[];
  }
) {
  const { directory, outputType, fileName, insightsId } = options;
  let res: { error?: string; chart_path?: string } = {};
  try {
    const specPath = getSavedPathName(directory, fileName, "json");
    const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
    // llm select index from 1
    const insights = (spec.insights || []).filter(
      (_insight: any, index: number) => insightsId.includes(index + 1)
    );
    const { newSpec, error } = await vmind.updateSpecByInsights(spec, insights);
    if (error) {
      throw error;
    }
    res.chart_path = await saveChartRes({
      spec: newSpec,
      directory,
      outputType,
      fileName,
    });
  } catch (error: any) {
    res.error = error.toString();
  } finally {
    return res;
  }
}

const writeResult = (res: object) => {
  process.stdout.write(`${RESULT_MARKER}${JSON.stringify(res)}\n`);
};

async function executeVMind() {
  const input = await readStdin();
  const inputData = JSON.parse(input);
  const {
    llm_config,
    width,
    dataset = [],
    height,
    directory,
    user_prompt: userPrompt,
    output_type: outputType = "png",
    file_name: fileName,
    task_type: taskType = "visualization",
    insights_id: insightsId = [],
    language = "en",
  } = inputData;
  const { base_url: baseUrl, model, api_key: apiKey } = llm_config;
  const vmind = new VMind({
    url: `${baseUrl}/chat/completions`,
    model,
    headers: {
      "api-key": apiKey,
      Authorization: `Bearer ${apiKey}`,
    },
  });
  if (taskType === "visualization") {
    return generateChart(vmind, {
      dataset,
      userPrompt,
      directory,
      outputType,
      fileName,
      width,
      height,
      language,
    });
  }
  if (taskType === "insight") {
    if (!insightsId.length) {
      return { error: "No insights were selected (insights_id is empty)" };
    }
    return updateChartWithInsight(vmind, {
      directory,
      fileName,
      outputType,
      insightsId,
    });
  }
  return { error: `Unknown task_type: ${taskType}` };
}

if (require.main === module) {
  executeVMind()
    .then(writeResult)
    .catch((error: any) => writeResult({ error: String(error?.message ?? error) }));
}
