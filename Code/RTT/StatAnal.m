%% =========================================================================
%  WiFi RTT Preprocessing Pipeline
%  Search & Rescue Vehicle — Range Estimation from ESP32 RTT Measurements
%
%  Supports CSV formats:
%   (A) wall_time, seq, timestamp_ms, rtt_raw_ns, rtt_est_ns, dist_cm, n_frames, status
%   (B) wall_time, seq, timestamp_ms, rtt_raw_ns, rtt_est_ns, dist_cm, status
%
%  Pipeline:
%   1. Load & parse one or more CSV files
%   2. Status filtering  (keep only successful measurements)
%   3. Physical sanity filtering  (hard bounds on distance)
%   4. IQR-based outlier rejection
%   5. Z-score outlier rejection  (second pass)
%   6. Temporal smoothing  (moving-median + Gaussian)
%   7. Statistical characterisation  (mean, median, std, CI, RMSE, …)
%   8. KDE-based range estimation  (mode of cleaned distribution)
%   9. Visualisation
%  10. Summary report
% =========================================================================

clear; clc; close all;

%% ── USER CONFIGURATION ──────────────────────────────────────────────────

% --- Files to process -------------------------------------------------------
% Leave as empty cell {} to open a GUI file picker instead.
CSV_FILES = {'FC1d25mS.csv'};          % e.g. {'run1.csv','run2.csv'}

% --- Physical plausibility bounds (cm) -------------------------------------
DIST_MIN_CM   = 30;      % below this → almost certainly a false reading
DIST_MAX_CM   = 50000000;    % upper physical limit for your test environment

% --- Outlier rejection thresholds ------------------------------------------
IQR_FACTOR    = 1.5;     % standard Tukey fence  (1.5 = mild, 3.0 = extreme)
ZSCORE_THRESH = 3.0;     % |z| > threshold → outlier

% --- Temporal smoothing -----------------------------------------------------
MEDFILT_WIN   = 7;       % samples for moving-median pre-smoother (odd)
GAUSS_SIGMA   = 2;       % σ for subsequent Gaussian kernel (samples)

% --- KDE range estimation ---------------------------------------------------
KDE_BANDWIDTH = 'silverman';   % 'silverman' | 'scott' | scalar (cm)

% --- Bootstrap confidence interval -----------------------------------------
BOOT_N        = 5000;    % bootstrap resamples for mean CI
CI_ALPHA      = 0.05;    % significance level  → 95 % CI

% ─────────────────────────────────────────────────────────────────────────────

%% ── 1. FILE SELECTION ────────────────────────────────────────────────────
if isempty(CSV_FILES)
    [fnames, fpath] = uigetfile('*.csv', ...
        'Select RTT CSV file(s) — hold Ctrl for multiple', ...
        'MultiSelect', 'on');
    if isequal(fnames, 0)
        error('No files selected. Aborting.');
    end
    if ischar(fnames), fnames = {fnames}; end
    CSV_FILES = fullfile(fpath, fnames);
end
nFiles = numel(CSV_FILES);
fprintf('\n=== RTT Preprocessing Pipeline ===\n');
fprintf('Files loaded : %d\n\n', nFiles);

%% ── 2. LOAD & CONCATENATE ALL FILES ──────────────────────────────────────
all_dist_raw   = [];
all_rtt_raw    = [];
all_rtt_est    = [];
all_time_ms    = [];
file_labels    = {};

for fi = 1:nFiles
    [~, fname, ~] = fileparts(CSV_FILES{fi});
    fprintf('[%d/%d] Parsing: %s\n', fi, nFiles, fname);

    T = parse_rtt_csv(CSV_FILES{fi});

    if isempty(T)
        warning('File %s yielded no valid rows — skipping.', fname);
        continue
    end

    n_raw = height(T);

% ── 2a. Status filter ──────────────────────────────────────────────
    if ismember('status', T.Properties.VariableNames)
        % Convert categories to a string array for easy searching
        ustat_str = string(unique(T.status));
        
        % Look for 'SUCCESS' (case-insensitive) or '0'
        is_success = strcmpi(ustat_str, 'SUCCESS') | ustat_str == "0";
        
        if any(is_success)
            % Grab the exact matching category
            success_code = categorical(ustat_str(is_success));
            success_code = success_code(1); % Keep the first if multiple match
        else
            % Fall back to the most frequent category if 'SUCCESS' isn't found
            [counts, cats] = histcounts(T.status);
            [~, max_idx]   = max(counts);
            success_code   = categorical(string(cats(max_idx)));
            warning(['Status "SUCCESS" not found. Using most-frequent value ' ...
                '"%s" as success — verify this is correct!'], string(success_code));
        end
        
        ok_status = (T.status == success_code);
        T = T(ok_status, :);
        fprintf('    Status filter (code="%s"): kept %d / %d rows\n', ...
            string(success_code), height(T), n_raw);
    end

    % ── 2b. Physical sanity bounds ────────────────────────────────────
    ok_phys = (T.dist_cm >= DIST_MIN_CM) & (T.dist_cm <= DIST_MAX_CM);
    T = T(ok_phys, :);
    fprintf('    Sanity bounds : kept %d rows  [%.0f–%.0f cm]\n', ...
        height(T), DIST_MIN_CM, DIST_MAX_CM);

    if height(T) < 5
        warning('Too few samples after filtering in %s — skipping.', fname);
        continue
    end

    all_dist_raw  = [all_dist_raw;  T.dist_cm];       
    all_rtt_raw   = [all_rtt_raw;   T.rtt_raw_ns];    
    all_rtt_est   = [all_rtt_est;   T.rtt_est_ns];    
    all_time_ms   = [all_time_ms;   T.timestamp_ms]; 

    file_labels   = [file_labels; ...
        repmat({fname}, height(T), 1)];               
end

if isempty(all_dist_raw)
    error('No valid data remained after loading all files.');
end

N_raw = numel(all_dist_raw);
fprintf('\nTotal samples after status + sanity filter : %d\n', N_raw);

%% ── 3. IQR OUTLIER REJECTION ─────────────────────────────────────────────
[dist_iqr, iqr_mask] = iqr_reject(all_dist_raw, IQR_FACTOR);
N_iqr = numel(dist_iqr);
fprintf('After IQR rejection (k=%.1f)              : %d  (removed %d)\n', ...
    IQR_FACTOR, N_iqr, N_raw - N_iqr);

%% ── 4. Z-SCORE OUTLIER REJECTION (second pass) ───────────────────────────
[dist_clean, z_mask] = zscore_reject(dist_iqr, ZSCORE_THRESH);
N_clean = numel(dist_clean);
fprintf('After Z-score rejection (|z|>%.1f)         : %d  (removed %d)\n', ...
    ZSCORE_THRESH, N_clean, N_iqr - N_clean);

% Propagate combined mask back to full index space for plotting
combined_mask = iqr_mask;
combined_mask(iqr_mask) = z_mask;   % logical indexing into kept set

%% ── 5. TEMPORAL SMOOTHING ────────────────────────────────────────────────
dist_medfilt = medfilt1(dist_clean, MEDFILT_WIN);
gauss_kernel = gausswin(6*GAUSS_SIGMA+1, GAUSS_SIGMA);
gauss_kernel = gauss_kernel / sum(gauss_kernel);
dist_smooth  = conv(dist_medfilt, gauss_kernel, 'same');

%% ── 6. STATISTICAL CHARACTERISATION ─────────────────────────────────────
stats = compute_statistics(dist_clean, dist_smooth, BOOT_N, CI_ALPHA);

fprintf('\n── Statistical Summary (cleaned, n=%d) ──\n', N_clean);
fprintf('  Mean            : %.2f cm\n',   stats.mean_raw);
fprintf('  Median          : %.2f cm\n',   stats.median_raw);
fprintf('  Std Dev         : %.2f cm\n',   stats.std_raw);
fprintf('  MAD             : %.2f cm\n',   stats.mad_raw);
fprintf('  IQR             : %.2f cm\n',   stats.iqr_raw);
fprintf('  RMSE vs median  : %.2f cm\n',   stats.rmse_raw);
fprintf('  Skewness        : %.3f\n',       stats.skew_raw);
fprintf('  Kurtosis        : %.3f\n',       stats.kurt_raw);
fprintf('  95%% Bootstrap CI: [%.2f, %.2f] cm\n', ...
    stats.ci_lo, stats.ci_hi);
fprintf('  Mean (smoothed) : %.2f cm\n',   stats.mean_smooth);
fprintf('  Std  (smoothed) : %.2f cm\n',   stats.std_smooth);

%% ── 7. KDE RANGE ESTIMATION ──────────────────────────────────────────────
[kde_x, kde_y, range_est] = kde_range_estimate(dist_clean, KDE_BANDWIDTH);

fprintf('\n── Range Estimation ──\n');
fprintf('  KDE mode (best range estimate) : %.2f cm  (= %.3f m)\n', ...
    range_est, range_est/100);
fprintf('  Mean estimate                  : %.2f cm\n', stats.mean_raw);
fprintf('  Median estimate                : %.2f cm\n', stats.median_raw);

%% ── 8. VISUALISATION ─────────────────────────────────────────────────────
plot_results(all_dist_raw, dist_clean, dist_smooth, ...
    all_rtt_raw, all_rtt_est, combined_mask, ...
    kde_x, kde_y, range_est, stats, file_labels, CI_ALPHA);

%% ── 9. EXPORT CLEANED DATA ──────────────────────────────────────────────
out_table = table(dist_clean, dist_smooth, ...
    'VariableNames', {'dist_clean_cm','dist_smooth_cm'});
writetable(out_table, 'rtt_cleaned_output.csv');
fprintf('\nCleaned data written to: rtt_cleaned_output.csv\n');

% Also export the key scalar outputs for use by the particle filter
summary.range_est_cm  = range_est;
summary.mean_cm       = stats.mean_raw;
summary.std_cm        = stats.std_raw;
summary.ci_lo_cm      = stats.ci_lo;
summary.ci_hi_cm      = stats.ci_hi;
summary.n_clean       = N_clean;
fprintf('\nRange estimate ready for particle filter:\n');
fprintf('  range_est_cm = %.2f,  std_cm = %.2f,  CI = [%.2f, %.2f]\n', ...
    summary.range_est_cm, summary.std_cm, summary.ci_lo_cm, summary.ci_hi_cm);


% ==========================================================================
%                           LOCAL FUNCTIONS
% ==========================================================================

%% ── parse_rtt_csv ─────────────────────────────────────────────────────────
function T = parse_rtt_csv(filepath)
% Robust RTT CSV parser.
%
% Handles:
%   - Comma-delimited  OR  whitespace-delimited files
%   - wall_time with embedded spaces (e.g. "2024-01-15 10:30:05.123")
%   - Text-based status columns (e.g., "success" or "failure")
%   - 7-column (no n_frames) and 8-column (with n_frames) layouts

    % ── Read all lines ──────────────────────────────────────────────────
    fid = fopen(filepath, 'r');
    if fid < 0
        warning('Cannot open file: %s', filepath);
        T = table(); return
    end
    lines = {};
    while ~feof(fid)
        ln = strtrim(fgetl(fid));
        if ischar(ln) && ~isempty(ln)
            lines{end+1} = ln; %#ok<AGROW>
        end
    end
    fclose(fid);
    if numel(lines) < 2
        warning('File has fewer than 2 lines: %s', filepath);
        T = table(); return
    end
    hdr_line  = lines{1};
    data_lines = lines(2:end);

    % ── Detect delimiter ────────────────────────────────────────────────
    n_comma = sum(hdr_line == ',');
    n_tab   = sum(hdr_line == char(9));
    if n_comma >= 3
        delim = ',';
    elseif n_tab >= 3
        delim = char(9);
    else
        delim = ' ';   % whitespace-separated
    end

    % ── Parse header names ──────────────────────────────────────────────
    hdr_parts = strip(strsplit(hdr_line, delim));
    hdr_parts = hdr_parts(~cellfun(@isempty, hdr_parts));
    nHdrCols  = numel(hdr_parts);
    fprintf('    Header columns (%d): %s\n', nHdrCols, strjoin(hdr_parts, ' | '));

    % Numeric trailing columns, EXCEPT status
    numeric_names_8 = {'seq','timestamp_ms','rtt_raw_ns','rtt_est_ns','dist_cm','n_frames'};
    numeric_names_7 = {'seq','timestamp_ms','rtt_raw_ns','rtt_est_ns','dist_cm'};
    
    hdr_lower = lower(hdr_parts);
    has_nframes = any(strcmp(hdr_lower, 'n_frames'));
    if has_nframes
        num_names = numeric_names_8;
    else
        num_names = numeric_names_7;
    end
    
    NUM_NUMERIC = numel(num_names);   % number of strictly numeric columns
    TOTAL_PAYLOAD_COLS = NUM_NUMERIC + 1; % numeric columns + 1 text status column
    
    WT_TOKENS = nHdrCols - TOTAL_PAYLOAD_COLS;
    if WT_TOKENS < 1
        WT_TOKENS = 1;  % safety
    end
    fprintf('    wall_time tokens: %d,  numeric payload columns: %d, plus status text\n', ...
        WT_TOKENS, NUM_NUMERIC);

    % ── Parse each data line ────────────────────────────────────────────
    wall_time_str = cell(numel(data_lines), 1);
    status_str    = cell(numel(data_lines), 1); % Pre-allocate for text status
    numeric_mat   = NaN(numel(data_lines), NUM_NUMERIC);
    n_good = 0;
    
    for li = 1:numel(data_lines)
        parts = strip(strsplit(data_lines{li}, delim));
        parts = parts(~cellfun(@isempty, parts));
        nP = numel(parts);
        if nP < TOTAL_PAYLOAD_COLS + 1
            continue   % not enough tokens
        end
        
        % wall_time = first (nP - TOTAL_PAYLOAD_COLS) tokens joined
        wt_end = nP - TOTAL_PAYLOAD_COLS;
        wt_str = strjoin(parts(1:wt_end), ' ');
        
        % Split the remaining payload into numeric parts and the final status string
        num_parts  = parts(wt_end+1 : end-1); 
        status_val = parts{end}; 
        
        if numel(num_parts) ~= NUM_NUMERIC
            continue
        end
        
        vals = str2double(num_parts);
        if any(isnan(vals))
            continue
        end
        
        n_good = n_good + 1;
        wall_time_str{n_good} = wt_str;
        numeric_mat(n_good, :) = vals;
        status_str{n_good}    = status_val;
    end
    
    if n_good == 0
        warning('No parseable data rows found in %s', filepath);
        T = table(); return
    end
    
    wall_time_str = wall_time_str(1:n_good);
    numeric_mat   = numeric_mat(1:n_good, :);
    status_str    = status_str(1:n_good);
    fprintf('    Parsed %d / %d data rows successfully\n', n_good, numel(data_lines));

    % ── Build table ──────────────────────────────────────────────────────
    T = table();
    T.wall_time = wall_time_str;
    for ci = 1:NUM_NUMERIC
        T.(num_names{ci}) = numeric_mat(:, ci);
    end
    T.status = categorical(status_str); % Converted to categorical for easier handling

    % ── Diagnostics: status values ──────────────────────────────────────
    if ismember('status', T.Properties.VariableNames)
        ustat = unique(T.status);
        % Because it is now categorical/text, we don't need arrayfun+num2str
        fprintf('    Unique status values: %s\n', strjoin(string(ustat), ', '));
    end

    % ── Diagnostics: dist_cm range ──────────────────────────────────────
    fprintf('    dist_cm range: [%.1f, %.1f] cm,  mean=%.1f\n', ...
        min(T.dist_cm), max(T.dist_cm), mean(T.dist_cm));
end

%% ── iqr_reject ────────────────────────────────────────────────────────────
function [x_clean, mask] = iqr_reject(x, k)
% Remove values outside  [Q1 - k*IQR,  Q3 + k*IQR]
    q1   = prctile(x, 25);
    q3   = prctile(x, 75);
    iqrv = q3 - q1;
    lo   = q1 - k * iqrv;
    hi   = q3 + k * iqrv;
    mask    = (x >= lo) & (x <= hi);
    x_clean = x(mask);
end

%% ── zscore_reject ─────────────────────────────────────────────────────────
function [x_clean, mask] = zscore_reject(x, thresh)
% Remove values with |z-score| > thresh  (uses robust z via MAD)
    med  = median(x);
    mad_ = mad(x, 1) * 1.4826;   % scaled MAD ≈ σ for Gaussian
    if mad_ < 1e-9
        % Fall back to regular z-score if MAD is degenerate
        z = (x - mean(x)) / (std(x) + 1e-9);
    else
        z = (x - med) / mad_;
    end
    mask    = abs(z) <= thresh;
    x_clean = x(mask);
end

%% ── compute_statistics ────────────────────────────────────────────────────
function s = compute_statistics(x, x_smooth, B, alpha)
    s.mean_raw   = mean(x);
    s.median_raw = median(x);
    s.std_raw    = std(x);
    s.mad_raw    = mad(x, 1);
    s.iqr_raw    = iqr(x);
    s.rmse_raw   = sqrt(mean((x - median(x)).^2));
    s.skew_raw   = skewness(x);
    s.kurt_raw   = kurtosis(x);
    s.mean_smooth = mean(x_smooth);
    s.std_smooth  = std(x_smooth);

    % Bootstrap CI for the mean
    rng(42);
    boot_means = zeros(B, 1);
    n = numel(x);
    for b = 1:B
        idx = randi(n, n, 1);
        boot_means(b) = mean(x(idx));
    end
    s.ci_lo = prctile(boot_means, 100 * alpha/2);
    s.ci_hi = prctile(boot_means, 100 * (1 - alpha/2));
end

%% ── kde_range_estimate ────────────────────────────────────────────────────
function [xq, fq, range_mode] = kde_range_estimate(x, bw_opt)
    n  = numel(x);
    xq = linspace(min(x) - 20, max(x) + 20, 1024)';

    if ischar(bw_opt)
        sigma = std(x);
        if strcmpi(bw_opt, 'silverman')
            h = 1.06 * sigma * n^(-1/5);
        else   % scott
            h = 1.059 * sigma * n^(-1/5);
        end
    else
        h = bw_opt;
    end

    % Gaussian KDE
    fq = zeros(size(xq));
    for i = 1:n
        fq = fq + exp(-0.5 * ((xq - x(i)) / h).^2);
    end
    fq = fq / (n * h * sqrt(2*pi));

    [~, idx] = max(fq);
    range_mode = xq(idx);
end

%% ── plot_results ──────────────────────────────────────────────────────────
function plot_results(dist_raw, dist_clean, dist_smooth, ...
        rtt_raw, rtt_est, inlier_mask, ...
        kde_x, kde_y, range_est, stats, labels, alpha)

    clr_raw     = [0.75 0.75 0.75];
    clr_outlier = [0.85 0.20 0.10];
    clr_clean   = [0.20 0.50 0.80];
    clr_smooth  = [0.10 0.70 0.30];
    clr_kde     = [0.90 0.45 0.00];
    clr_est     = [0.60 0.00 0.60];

    figure('Name','RTT Preprocessing Pipeline', ...
        'NumberTitle','off','Color','w','Position',[80 80 1400 900]);

    % Changed to 4x3 to comfortably fit all 10 tile slots
    tiledlayout(4, 3, 'TileSpacing','compact','Padding','compact');

    N_all = numel(dist_raw);
    t_all = (1:N_all)';
    t_clean = find(inlier_mask);

    % ── Panel 1: Raw vs Cleaned time-series ──────────────────────────────
    nexttile([1 2]);
    hold on; grid on; box on;
    plot(t_all, dist_raw, '.', 'Color', clr_raw, 'MarkerSize', 5, ...
        'DisplayName','Raw (all)');
    outlier_idx = t_all(~inlier_mask);
    plot(outlier_idx, dist_raw(~inlier_mask), 'x', ...
        'Color', clr_outlier, 'MarkerSize', 7, 'LineWidth', 1.5, ...
        'DisplayName','Rejected outliers');
    plot(t_clean, dist_clean, '.', 'Color', clr_clean, 'MarkerSize', 6, ...
        'DisplayName','Cleaned');
    plot(t_clean, dist_smooth, '-', 'Color', clr_smooth, 'LineWidth', 1.8, ...
        'DisplayName','Smoothed');
    yline(range_est, '--', 'Color', clr_est, 'LineWidth', 1.5, ...
        'Label', sprintf('KDE est. %.1f cm', range_est), ...
        'LabelHorizontalAlignment','right', 'DisplayName','KDE mode');
    xlabel('Sample index'); ylabel('Distance (cm)');
    title('Distance Time-Series: Raw → Cleaned → Smoothed');
    legend('Location','best','FontSize',8); hold off;

    % ── Panel 2: RTT scatter (raw vs est) ────────────────────────────────
    nexttile;
    if ~isempty(rtt_raw) && ~isempty(rtt_est)
        hold on; grid on; box on;
        scatter(rtt_raw(inlier_mask)/1000, rtt_est(inlier_mask)/1000, 18, ...
            clr_clean, 'filled', 'MarkerFaceAlpha', 0.5);
        scatter(rtt_raw(~inlier_mask)/1000, rtt_est(~inlier_mask)/1000, 18, ...
            clr_outlier, 'filled', 'MarkerFaceAlpha', 0.5);
        lims = [min([rtt_raw;rtt_est]) max([rtt_raw;rtt_est])]/1000;
        plot(lims, lims, 'k--', 'LineWidth', 1, 'DisplayName','y = x');
        xlabel('RTT raw (µs)'); ylabel('RTT estimated (µs)');
        title('RTT Raw vs Estimated');
        legend({'Inliers','Outliers','y=x'},'Location','best','FontSize',8);
        hold off;
    else
        axis off; text(0.5,0.5,'RTT data not available','HorizontalAlignment','center');
    end

    % ── Panel 3: KDE + histogram + CI ────────────────────────────────────
    nexttile([1 2]);
    hold on; grid on; box on;
    h_hist = histogram(dist_clean, 'Normalization','pdf', ...
        'FaceColor', clr_clean, 'FaceAlpha', 0.35, 'EdgeColor','none');
    plot(kde_x, kde_y, '-', 'Color', clr_kde, 'LineWidth', 2.5, ...
        'DisplayName','KDE');
    xline(range_est, '--', 'Color', clr_est, 'LineWidth', 2, ...
        'Label', sprintf('Mode %.1f cm', range_est), ...
        'LabelHorizontalAlignment','right');
    xline(stats.mean_raw, '-.', 'Color',[0 0 0], 'LineWidth', 1.5, ...
        'Label', sprintf('Mean %.1f cm', stats.mean_raw), ...
        'LabelHorizontalAlignment','left');
    xline(stats.median_raw, ':', 'Color',[0.3 0.3 0.3], 'LineWidth', 1.8, ...
        'Label', sprintf('Median %.1f cm', stats.median_raw), ...
        'LabelHorizontalAlignment','left');
    % CI shading
    ci_mask = (kde_x >= stats.ci_lo) & (kde_x <= stats.ci_hi);
    if any(ci_mask)
        area(kde_x(ci_mask), kde_y(ci_mask), ...
            'FaceColor',[0.8 0.6 1.0],'FaceAlpha',0.4,'EdgeColor','none', ...
            'DisplayName',sprintf('%.0f%% CI mean', 100*(1-alpha)));
    end
    xlabel('Distance (cm)'); ylabel('Probability density');
    title('Distribution of Cleaned Distances + KDE Range Estimate');
    legend('Location','best','FontSize',8); hold off;

    % ── Panel 4: Box plot comparison (Upgraded to boxchart) ───────────────
    nexttile;
    g_raw    = repmat({'1 Raw'},     numel(dist_raw),   1);
    g_clean  = repmat({'2 Clean'},   numel(dist_clean), 1);
    g_smooth = repmat({'3 Smooth'},  numel(dist_smooth),1);
    
    % Use modern categorical grouping for boxchart
    groups = categorical([g_raw; g_clean; g_smooth]);
    vals   = [dist_raw; dist_clean; dist_smooth];
    
    boxchart(groups, vals, 'MarkerStyle', 'o');
    ylabel('Distance (cm)'); title('Box Plot: Raw | Cleaned | Smoothed');
    grid on; box on;

    % ── Panel 5: Q-Q plot of cleaned data ─────────────────────────────────
    nexttile;
    qqplot(dist_clean);
    title('Q-Q Plot (Cleaned vs Normal)');
    grid on;

    % ── Panel 6: Residuals after smoothing ────────────────────────────────
    nexttile([1 2]);
    resid = dist_clean - dist_smooth;
    hold on; grid on; box on;
    stem(t_clean, resid, 'Color', clr_clean, 'MarkerSize', 3, ...
        'LineWidth', 0.6, 'DisplayName','Residual');
    yline(0, 'k-', 'LineWidth', 1);
    yline( 2*std(resid), 'r--', 'LineWidth', 1, 'Label','+2σ');
    yline(-2*std(resid), 'r--', 'LineWidth', 1, 'Label','-2σ');
    xlabel('Sample index'); ylabel('Residual (cm)');
    title('Residuals: Cleaned − Smoothed  (should be zero-mean white noise)');
    legend('Location','best','FontSize',8); hold off;

    % ── Panel 7: ACF of residuals ─────────────────────────────────────────
    nexttile;
    max_lag = min(40, floor(numel(resid)/4));
    [acf_vals, lags] = xcorr(resid - mean(resid), max_lag, 'coeff');
    stem(lags, acf_vals, 'filled', 'Color', clr_kde, 'MarkerSize', 3);
    hold on;
    conf = 1.96 / sqrt(numel(resid));
    yline( conf, 'r--', 'LineWidth', 1);
    yline(-conf, 'r--', 'LineWidth', 1);
    yline(0, 'k-');
    xlabel('Lag (samples)'); ylabel('Autocorrelation');
    title('ACF of Smoothing Residuals');
    xlim([-max_lag max_lag]); ylim([-1.1 1.1]);
    grid on; box on; hold off;

    sgtitle(sprintf(['RTT Preprocessing Report   |   ' ...
        'Range estimate: \\bf%.1f cm\\rm  ±%.1f cm  (σ)   |  n_{clean} = %d'], ...
        range_est, stats.std_raw, numel(dist_clean)), 'FontSize', 13);
end