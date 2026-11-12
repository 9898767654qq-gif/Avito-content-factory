(function () {
  const categories = JSON.parse(document.getElementById("category-data").textContent);
  const examples = JSON.parse(document.getElementById("example-data").textContent);
  const initial = JSON.parse(document.getElementById("initial-data").textContent);
  const select = document.getElementById("category");
  const extraBox = document.getElementById("extra-fields");
  const productList = document.getElementById("product-types");
  const productInput = document.getElementById("product-type");
  const lead = document.getElementById("category-lead");

  function currentExtra() {
    const values = {};
    extraBox.querySelectorAll("input").forEach((input) => {
      values[input.name.replace("extra__", "")] = input.value;
    });
    return values;
  }

  function render(extra) {
    const category = categories.find((item) => item.slug === select.value) || categories[0];
    extraBox.replaceChildren();
    productList.replaceChildren();
    category.product_types.forEach((type) => {
      const option = document.createElement("option");
      option.value = type;
      productList.appendChild(option);
    });
    category.fields.forEach((field) => {
      const label = document.createElement("label");
      label.className = "field";
      const span = document.createElement("span");
      span.className = "field-label";
      span.textContent = field.required ? field.label + " *" : field.label;
      const input = document.createElement("input");
      input.name = "extra__" + field.key;
      input.maxLength = 200;
      input.placeholder = field.placeholder || "";
      input.value = (extra && extra[field.key]) || "";
      if (field.required) input.required = true;
      label.append(span, input);
      extraBox.appendChild(label);
    });
    if (lead) lead.textContent = category.lead;
  }

  select.addEventListener("change", () => render(currentExtra()));

  document.querySelectorAll("[data-example-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const example = examples.find((item) => item.id === button.dataset.exampleId);
      if (!example) return;
      select.value = example.category;
      productInput.value = example.product_type;
      document.getElementById("target-buyer").value = example.target_buyer;
      document.getElementById("tone").value = example.tone;
      document.getElementById("price").value = example.price;
      document.getElementById("region").value = example.region;
      document.getElementById("features").value = example.key_features.join("\n");
      document.getElementById("budget").value = example.promotion_budget ?? "";
      render(example.extra || {});
    });
  });

  render(initial.extra || {});
})();
