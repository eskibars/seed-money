// Seed Money — Frontend interactions

document.addEventListener("DOMContentLoaded", () => {
    // Scoring preset toggle
    const presetSelect = document.getElementById("scoring_preset");
    const customSection = document.getElementById("custom-scoring");

    if (presetSelect && customSection) {
        presetSelect.addEventListener("change", () => {
            customSection.classList.toggle("hidden", presetSelect.value !== "custom");
        });
    }

    // Accuracy weight slider label
    const slider = document.getElementById("accuracy_weight");
    const valueDisplay = document.getElementById("accuracy-value");

    if (slider && valueDisplay) {
        slider.addEventListener("input", () => {
            valueDisplay.textContent = parseFloat(slider.value).toFixed(2);
        });
    }

    // Upset bonus mode toggle
    const upsetMode = document.getElementById("upset_mode");
    const multFields = document.getElementById("upset-multiplier-fields");
    const fixedFields = document.getElementById("upset-fixed-fields");

    function updateUpsetFields() {
        if (!upsetMode) return;
        const mode = upsetMode.value;

        // Show/hide field groups
        if (multFields) multFields.classList.toggle("hidden", mode !== "multiplier");
        if (fixedFields) fixedFields.classList.toggle("hidden", mode !== "fixed");

        // Disable hidden inputs so only the active set submits
        if (multFields) {
            multFields.querySelectorAll("input").forEach(inp => {
                inp.disabled = mode !== "multiplier";
            });
        }
        if (fixedFields) {
            fixedFields.querySelectorAll("input").forEach(inp => {
                inp.disabled = mode !== "fixed";
            });
        }
    }

    if (upsetMode) {
        upsetMode.addEventListener("change", updateUpsetFields);
        updateUpsetFields(); // Initialize on page load
    }

    // Add bias row
    const addBiasBtn = document.getElementById("add-bias");
    const biasContainer = document.getElementById("biases-container");

    if (addBiasBtn && biasContainer) {
        addBiasBtn.addEventListener("click", () => {
            const row = document.createElement("div");
            row.className = "bias-row";
            row.innerHTML = `
                <input type="text" name="bias_team" list="team-list" placeholder="Team name" autocomplete="off">
                <select name="bias_direction">
                    <option value="over-picked">Over-picked</option>
                    <option value="under-picked">Under-picked</option>
                </select>
                <select name="bias_magnitude">
                    <option value="slight">Slight</option>
                    <option value="moderate">Moderate</option>
                    <option value="heavy">Heavy</option>
                </select>
                <button type="button" class="btn-icon remove-bias" title="Remove">&times;</button>
            `;
            biasContainer.appendChild(row);
        });

        // Remove bias row (event delegation)
        biasContainer.addEventListener("click", (e) => {
            if (e.target.classList.contains("remove-bias")) {
                const row = e.target.closest(".bias-row");
                if (biasContainer.children.length > 1) {
                    row.remove();
                } else {
                    // Clear the last row instead of removing
                    row.querySelector("input").value = "";
                }
            }
        });
    }

    // Form submission — disable button to prevent double submit
    const form = document.getElementById("optimize-form");
    const submitBtn = document.getElementById("submit-btn");

    if (form && submitBtn) {
        form.addEventListener("submit", () => {
            submitBtn.disabled = true;
            submitBtn.textContent = "Submitting...";
        });
    }
});
