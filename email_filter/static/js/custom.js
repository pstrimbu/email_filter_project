document.addEventListener('DOMContentLoaded', function() {
    console.log('JavaScript loaded successfully');

    let selectedAccountId = null; // Global variable to store the selected account ID

    const contentPane = document.getElementById('contentPane');
    const loadingSpinner = document.getElementById('loadingSpinner');

    const emailAccountSelect = document.getElementById('emailAccountSelect');
    if (emailAccountSelect) {
        selectedAccountId = emailAccountSelect.value;

        if (!selectedAccountId) {
            fetch('/get_email_accounts')
                .then(response => response.json())
                .then(data => {
                    if (data.success && data.email_accounts.length > 0) {
                        selectedAccountId = data.email_accounts[0].id;
                        emailAccountSelect.value = selectedAccountId; // Set the select element to the first account
                        console.log('Selected account ID:', selectedAccountId);
                    }
                })
                .catch(error => console.error('Error fetching email accounts:', error));
        }

        emailAccountSelect.addEventListener('change', function() {
            selectedAccountId = emailAccountSelect.value;
        });
    }

    function saveOrder(type, rows) {
        const items = rows.map(row => {
            const id = row.getAttribute('data-filter-id') || row.getAttribute('data-id');
            const order = Array.from(row.parentNode.children).indexOf(row);
            return { id: id, order: order };
        });

        const csrfToken = document.querySelector('input[name="csrf_token"]').value;
        fetch(`/${type}/reorder`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ items: items })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    displayFlashMessage(`${type.charAt(0).toUpperCase() + type.slice(1)} order saved successfully!`, 'success');
                } else {
                    displayFlashMessage(`Failed to save ${type} order: ` + data.message, 'danger');
                }
            })
            .catch(error => console.error(`Error saving ${type} order:`, error));
    }

    function initializeComponents() {

        const emailFolderTable = document.getElementById('emailFolderTable');
        if (emailFolderTable)
            updateEmailFolderTable()

        // const filterActionButtons = document.querySelectorAll('.filter-action-button');
        // if (filterActionButtons && filterActionButtons.length > 0) {
        //     const filterActionInput = document.getElementById('filter_action');
        //     filterActionButtons.forEach(button => {
        //         button.addEventListener('click', function() {
        //             filterActionButtons.forEach(btn => {
        //                 btn.classList.remove('btn-primary', 'btn-success', 'btn-danger');
        //                 btn.classList.add('btn-secondary');
        //             });

        //             this.classList.remove('btn-secondary');
        //             if (this.getAttribute('data-action') === 'include') {
        //                 this.classList.add('btn-success');
        //             } else if (this.getAttribute('data-action') === 'exclude') {
        //                 this.classList.add('btn-danger');
        //             }

        //             filterActionInput.value = this.getAttribute('data-action');
        //         });
        //     });

        //     const defaultAction = filterActionInput.value;
        //     const defaultButton = document.querySelector(`.filter-action-button[data-action="${defaultAction}"]`);
        //     if (defaultButton) {
        //         defaultButton.classList.remove('btn-secondary');
        //         if (defaultAction === 'include') {
        //             defaultButton.classList.add('btn-success');
        //         } else if (defaultAction === 'exclude') {
        //             defaultButton.classList.add('btn-danger');
        //         }
        //     }
        // }

        const addFilterButton = document.getElementById('addFilterButton');
        if (addFilterButton) {
            addFilterButton.addEventListener('click', function() {
                const newRow = document.createElement('tr');
                newRow.innerHTML = `
                    <td></td>
                    <td></td>
                    <td></td>
                    <td>
                        <input type="text" class="form-control filter-text" placeholder="Enter filter word/phrase">
                    </td>
                    <td class="action-buttons-td">
                        <button class="btn btn-sm btn-success action-button save-filter"><i class="fas fa-check"></i></button>
                        <button class="btn btn-sm btn-danger action-button cancel-filter"><i class="fas fa-times"></i></button>
                    </td>
                `;
                filtersTableBody.appendChild(newRow);

                // Add event listeners for the new buttons
                newRow.querySelector('.save-filter').addEventListener('click', function() {
                    if (!newRow.querySelector('.filter-text')) {
                        displayFlashMessage('Filter text is required', 'danger');
                        return;
                    }
                    const filterText = newRow.querySelector('.filter-text').value;
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;
                    const newAction = newRow.querySelector('.filter-action-toggle.btn-success') ? 'exclude' : 'include';

                    fetch('/filters', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': csrfToken
                            },
                            body: JSON.stringify({ filter: filterText, action: newAction, account_id: selectedAccountId })
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage('Filter added successfully!', 'success');
                                simulateClick('filters-item'); // Simulate click to reload filters
                            } else {
                                displayFlashMessage('Failed to add filter: ' + data.message, 'danger');
                            }
                        })
                        .catch(error => console.error('Error adding filter:', error));
                });

                newRow.querySelector('.cancel-filter').addEventListener('click', function() {
                    newRow.remove();
                });
            });
        }

        const filtersTableBody = document.getElementById('filtersTableBody');
        if (filtersTableBody) {
            filtersTableBody.addEventListener('click', function(e) {
                if (e.target.closest('.delete-filter')) {
                    const filterId = e.target.closest('tr').getAttribute('data-filter-id');
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                    fetch(`/delete_filter/${filterId}`, {
                            method: 'POST',
                            headers: {
                                'X-CSRFToken': csrfToken
                            }
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage('Filter deleted successfully!', 'success');
                                e.target.closest('tr').remove();
                                // simulateClick('filters_view');
                            } else {
                                displayFlashMessage('Failed to delete filter: ' + data.error, 'danger');
                            }
                        })
                        .catch(error => console.error('Error deleting filter:', error));
                } else if (e.target.closest('.move-up')) {
                    const row = e.target.closest('tr');
                    const previousRow = row.previousElementSibling;
                    if (previousRow) {
                        filtersTableBody.insertBefore(row, previousRow);
                        saveOrder('filters', [row, previousRow]);
                    }
                } else if (e.target.closest('.move-down')) {
                    const row = e.target.closest('tr');
                    const nextRow = row.nextElementSibling;
                    if (nextRow) {
                        filtersTableBody.insertBefore(nextRow, row);
                        saveOrder('filters', [row, nextRow]);
                    }
                } else if (e.target.closest('.filter-action-toggle')) {
                    const button = e.target.closest('.filter-action-toggle');
                    const filterId = button.getAttribute('data-filter-id');
                    const newAction = button.getAttribute('data-action');
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                    fetch(`/update_filter_action/${filterId}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': csrfToken
                            },
                            body: JSON.stringify({ action: newAction })
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage('Filter action updated successfully!', 'success');
                                // Update button styles
                                button.classList.remove('btn-secondary', 'btn-success', 'btn-danger');
                                button.classList.add(newAction === 'include' ? 'btn-success' : 'btn-danger');
                                // Reset other button styles in the same group
                                const siblingButton = button.parentElement.querySelector(`button[data-action="${newAction === 'include' ? 'exclude' : 'include'}"]`);
                                siblingButton.classList.remove('btn-success', 'btn-danger');
                                siblingButton.classList.add('btn-secondary');
                            } else {
                                displayFlashMessage('Failed to update filter action: ' + data.message, 'danger');
                            }
                        })
                        .catch(error => console.error('Error updating filter action:', error));
                }
            });
        }
        // Ensure the button exists and add an event listener
        const testConnectionButton = document.getElementById('testConnectionButton');
        if (testConnectionButton) {
            testConnectionButton.addEventListener('click', function(e) {
                e.preventDefault();
                const accountId = this.getAttribute('data-account-id');
                if (accountId) {
                    testEmailConnection(accountId);
                } else {
                    console.error('Account ID not found.');
                }
            });
        }

        // Ensure the button exists and add an event listener
        const testNewEmailAccountButton = document.getElementById('testNewEmailAccountButton');
        if (testNewEmailAccountButton) {
            testNewEmailAccountButton.addEventListener('click', function(e) {
                e.preventDefault();
                const emailAccountForm = document.getElementById('EmailAccountForm');
                const formData = new FormData(emailAccountForm);
                const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                fetch('/test_new_email_connection', {
                        method: 'POST',
                        body: formData,
                        headers: {
                            'X-CSRFToken': csrfToken
                        }
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            displayFlashMessage('Connection successful!', 'success');
                        } else {
                            displayFlashMessage('Connection failed: ' + data.error, 'danger');
                        }
                    })
                    .catch(error => console.error('Error testing connection:', error));
            });
        }

        // Add event listener for the Clear Emails button
        const deleteEmailsButton = document.getElementById('deleteEmailsButton');
        if (deleteEmailsButton) {
            deleteEmailsButton.addEventListener('click', function(e) {
                e.preventDefault();
                const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                if (confirm('Are you sure you want to clear all emails for this account?')) {
                    fetch('/delete_emails/' + selectedAccountId, {
                            method: 'POST',
                            headers: { 'X-CSRFToken': csrfToken }
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage('Emails cleared successfully.', 'success');
                                // Refresh the page content without a full reload
                                fetch(`/emails`)
                                    .then(response => response.text())
                                    .then(html => {
                                        contentPane.innerHTML = html;
                                    })
                                    .catch(error => {
                                        displayFlashMessage('Error refreshing page content: ' + error, 'danger');
                                        console.error('Error refreshing page content:', error);
                                    });
                            } else {
                                displayFlashMessage('Failed to clear emails: ' + data.message, 'danger');
                            }
                        })
                        .catch(error => {
                            displayFlashMessage('Error clearing emails: ' + error, 'danger');
                            console.error('Error clearing emails:', error);
                        });

                }
            });
        }

        // Add event listener for the Scan Emails button
        const scanEmailsButton = document.getElementById('scanEmailsButton');
        if (scanEmailsButton) {
            scanEmailsButton.addEventListener('click', function(e) {
                e.preventDefault();

                const csrfToken = document.querySelector('input[name="csrf_token"]').value;
                // Turn on live update toggle
                liveUpdateToggle.checked = true;
                liveUpdateToggle.dispatchEvent(new Event('change'));

                fetch('/scan_emails/' + selectedAccountId, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': csrfToken
                        }
                    })
                    .then(response => {
                        if (!response.ok) {
                            throw new Error('Network response was not ok');
                        }
                        return response.json();
                    })
                    .then(data => {
                        if (data.success) {
                            displayFlashMessage('Scanned emails.', 'success');
                        } else {
                            displayFlashMessage('Failed to scan emails: ' + data.error, 'danger');
                        }
                    })
                    .catch(error => {
                        console.error('Error scanning emails:', error);
                        alert('An error occurred while scanning emails. Please check the console for more details.');
                    });
            });
        }

        // Stop button logic
        const stopButton = document.getElementById('stopButton');
        if (stopButton) {
            stopButton.addEventListener('click', function(e) {
                e.preventDefault();

                fetch(`/stop_scan/${selectedAccountId}`, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': document.querySelector('input[name="csrf_token"]').value
                        }
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            displayFlashMessage('Scan stopped successfully.', 'success');
                        } else {
                            displayFlashMessage('Failed to stop scan: ' + data.error, 'danger');
                        }
                    })
                    .catch(error => {
                        displayFlashMessage('Error stopping scan: ' + error, 'danger');
                        console.error('Error stopping scan:', error);
                    });
            });
        }

        // Live update logic
        const liveUpdateToggle = document.getElementById('liveUpdateToggle');
        const liveUpdateLabel = document.querySelector('label[for="liveUpdateToggle"]');
        if (liveUpdateToggle) {
            let liveUpdateInterval;
            liveUpdateToggle.addEventListener('change', function() {
                if (this.checked) {
                    // Start live updates
                    liveUpdateInterval = setInterval(() => {
                        const runningIndicator = document.getElementById('runningIndicator');
                        if (!runningIndicator) {
                            clearInterval(liveUpdateInterval);
                            console.log('Running indicator not found. stopping live update.');
                            return;
                        }
                        // Flash the "Live Update" text
                        liveUpdateLabel.classList.add('bold-flash');
                        setTimeout(() => {
                            liveUpdateLabel.classList.remove('bold-flash');
                        }, 500); // Remove the class after 500ms

                        updateEmailFolderTable();
                        checkScanStatus(selectedAccountId);
                    }, 5000); // 5 seconds
                } else {
                    // Stop live updates
                    clearInterval(liveUpdateInterval);
                    liveUpdateLabel.classList.remove('bold-flash'); // Ensure label style is reset
                }
            });
        }

        const promptsTableBody = document.getElementById('promptsTableBody');
        if (promptsTableBody) {
            promptsTableBody.addEventListener('input', function(e) {
                if (e.target.matches('.prompt-text')) {
                    const row = e.target.closest('tr');
                    const originalText = e.target.defaultValue;
                    const currentText = e.target.value;
                    const saveButton = row.querySelector('.save-prompt'); // existing row save button
                    const cancelButton = row.querySelector('.cancel-prompt');

                    if (currentText !== originalText) {
                        saveButton.disabled = false;
                        cancelButton.disabled = false;
                    } else {
                        saveButton.disabled = true;
                        cancelButton.disabled = true;
                    }
                }
            });
            promptsTableBody.addEventListener('click', function(e) {
                if (e.target.closest('.move-up')) {
                    const row = e.target.closest('tr');
                    const previousRow = row.previousElementSibling;
                    if (previousRow) {
                        promptsTableBody.insertBefore(row, previousRow);
                        saveOrder('prompts', [row, previousRow]);
                    }
                } else if (e.target.closest('.move-down')) {
                    const row = e.target.closest('tr');
                    const nextRow = row.nextElementSibling;
                    if (nextRow) {
                        promptsTableBody.insertBefore(nextRow, row);
                        saveOrder('prompts', [row, nextRow]);
                    }
                } else if (e.target.closest('.delete-prompt')) {
                    const row = e.target.closest('tr');
                    const promptId = row.getAttribute('data-id');
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                    fetch(`/delete_prompt/${promptId}`, {
                            method: 'POST',
                            headers: {
                                'X-CSRFToken': csrfToken
                            }
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage(data.message, 'success');
                                row.remove(); // Remove the row from the table
                            } else {
                                displayFlashMessage(data.message, 'danger');
                            }
                        })
                        .catch(error => console.error('Error deleting prompt:', error));
                } else if (e.target.closest('.prompt-action-toggle')) {
                    const button = e.target.closest('.prompt-action-toggle');
                    const promptId = button.getAttribute('data-prompt-id');
                    const newAction = button.getAttribute('data-action');
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                    fetch(`/update_prompt_action/${promptId}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': csrfToken
                            },
                            body: JSON.stringify({ action: newAction })
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage('Prompt action updated successfully!', 'success');
                                // Update button styles
                                button.classList.remove('btn-secondary', 'btn-success', 'btn-danger');
                                button.classList.add(newAction === 'include' ? 'btn-success' : 'btn-danger');
                                // Reset other button styles in the same group
                                const siblingButton = button.parentElement.querySelector(`button[data-action="${newAction === 'include' ? 'exclude' : 'include'}"]`);
                                siblingButton.classList.remove('btn-success', 'btn-danger');
                                siblingButton.classList.add('btn-secondary');
                            } else {
                                displayFlashMessage('Failed to update prompt action: ' + data.message, 'danger');
                            }
                        })
                        .catch(error => console.error('Error updating prompt action:', error));
                }
            });
        }

        const addPromptButton = document.getElementById('addPromptButton');
        if (addPromptButton) {
            addPromptButton.addEventListener('click', function() {
                const newRow = document.createElement('tr');
                newRow.innerHTML = `
                    <td></td>
                    <td></td>
                    <td>
                        <textarea class="form-control prompt-text" rows="1"></textarea>
                    </td>
                    <td class="action-buttons-td">
                        <button class="btn btn-sm btn-success action-button save-prompt"><i class="fas fa-check"></i></button>
                        <button class="btn btn-sm btn-danger action-button cancel-prompt"><i class="fas fa-times"></i></button>
                    </td>
                `;
                promptsTableBody.appendChild(newRow);

                // Add event listeners for the new buttons
                newRow.querySelector('.save-prompt').addEventListener('click', function() {
                    const promptText = newRow.querySelector('.prompt-text').value;
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                    // Assuming you have a way to determine the action, e.g., a toggle button or a default value
                    const newAction = 'include'; // or 'exclude', based on your logic

                    fetch('/ai_prompts', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': csrfToken
                            },
                            body: JSON.stringify({ id: null, account_id: selectedAccountId, prompt_text: promptText, order: promptsTableBody.children.length - 1, action: newAction })
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage(data.message, 'success');
                                simulateClick('ai-prompts-item'); // Simulate click to reload AI prompts
                            } else {
                                displayFlashMessage(data.message, 'danger');
                            }
                        })
                        .catch(error => console.error('Error saving prompt:', error));
                });

                newRow.querySelector('.cancel-prompt').addEventListener('click', function() {
                    newRow.remove();
                });
            });
        }

        // Live Update Toggle Logic
        const liveResultsUpdateToggle = document.getElementById('liveResultsUpdateToggle');
        const liveResultsUpdateLabel = document.querySelector('label[for="liveResultsUpdateToggle"]');
        let liveResultsUpdateInterval;

        if (liveResultsUpdateToggle) {
            liveResultsUpdateToggle.addEventListener('change', function() {
                if (this.checked) {
                    // Start live updates
                    liveResultsUpdateInterval = setInterval(() => {
                        // Flash the "Live Update" text
                        liveResultsUpdateLabel.classList.add('bold-flash');
                        setTimeout(() => {
                            liveResultsUpdateLabel.classList.remove('bold-flash');
                        }, 500); // Remove the class after 500ms

                        renderProcessEmailResultsView();
                    }, 5000); // 5 seconds
                } else {
                    // Stop live updates
                    clearInterval(liveResultsUpdateInterval);
                    liveResultsUpdateLabel.classList.remove('bold-flash'); // Ensure label style is reset
                }
            });
        }

        const processResultsButton = document.getElementById('processResultsButton');
        if (processResultsButton) {
            processResultsButton.addEventListener('click', function() {
                const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                // Enable Live Update
                liveResultsUpdateToggle.checked = true;
                liveResultsUpdateToggle.dispatchEvent(new Event('change'));

                fetch('/process_email_results', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken
                        },
                        body: JSON.stringify({ account_id: selectedAccountId }),
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            displayFlashMessage('Results processed successfully!', 'success');
                            renderProcessEmailResultsView();
                        } else {
                            displayFlashMessage('Failed to process results: ' + data.error, 'danger');
                        }
                    })
                    .catch(error => console.error('Error processing emails:', error));
            });
        }

        const resultsFilesList = document.getElementById('resultsFilesList');
        if (resultsFilesList) {
            resultsFilesList.addEventListener('click', function(e) {
                if (e.target.closest('.delete-file-btn')) {
                    const row = e.target.closest('tr');
                    const fileId = row.getAttribute('data-file-id');
                    const csrfToken = document.querySelector('input[name="csrf_token"]').value;

                    fetch(`/delete_file/${fileId}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': csrfToken
                            }
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                displayFlashMessage('File deleted successfully!', 'success');
                                row.querySelector('td:first-child').textContent = 'deleted';
                                row.querySelector('a').remove(); // Remove the download link
                            } else {
                                displayFlashMessage('Failed to delete file: ' + data.message, 'danger');
                            }
                        })
                        .catch(error => console.error('Error deleting file:', error));
                }
            });
        }

        const limitDatesCheckbox = document.getElementById('limitDates');
        const startDateInput = document.getElementById('start_date');
        const endDateInput = document.getElementById('end_date');

        if (limitDatesCheckbox) {
            limitDatesCheckbox.addEventListener('change', function() {
                if (this.checked) {
                    startDateInput.setAttribute('required', 'required');
                    endDateInput.setAttribute('required', 'required');
                    dateSelectors.style.display = 'flex';
                } else {
                    startDateInput.removeAttribute('required');
                    endDateInput.removeAttribute('required');
                    dateSelectors.style.display = 'none';
                    startDateInput.value = ''; // Set start date to None
                    endDateInput.value = ''; // Set end date to None
                }
            });

            // Initial check to set the required attribute based on the current state
            if (limitDatesCheckbox.checked) {
                startDateInput.setAttribute('required', 'required');
                endDateInput.setAttribute('required', 'required');
            } else {
                startDateInput.removeAttribute('required');
                endDateInput.removeAttribute('required');
            }
        }
    }

    initializeComponents();





    function renderProcessEmailResultsView() {
        const csrfToken = document.querySelector('input[name="csrf_token"]').value;
        const url = new URL(`/process_email_results_view`, window.location.origin);
        url.searchParams.append('account_id', selectedAccountId);

        fetch(url, {
                method: 'GET',
                headers: {
                    'X-CSRFToken': csrfToken
                }
            })
            .then(response => response.text()) // Use response.text() to get the HTML
            .then(html => {
                // Create a temporary DOM element to parse the HTML
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                // Extract the necessary elements from the parsed HTML
                const statusElement = doc.querySelector('#processResultsStatus');
                const logElement = doc.querySelector('#processResultsLog');
                const resultsFilesTable = doc.querySelector('#resultsFilesTable');

                if (statusElement && logElement) {
                    const status = statusElement.textContent.trim();
                    const logEntry = logElement.value.trim();

                    // Update the current page with the extracted information
                    document.getElementById('processResultsStatus').textContent = status;
                    document.getElementById('processResultsLog').value = logEntry;

                    if (status === 'Status: finished' || status === 'Status: error') {
                        // Disable Live Update
                        liveResultsUpdateToggle.checked = false;
                        liveResultsUpdateToggle.dispatchEvent(new Event('change'));

                        if (resultsFilesTable && resultsFilesTable.querySelector('tbody').children.length > 0) {
                            resultsFilesTable.style.display = 'table';
                        } else {
                            resultsFilesTable.style.display = 'none';
                        }
                    } else {
                        resultsFilesTable.style.display = 'none';
                    }
                } else {
                    console.error('Failed to extract process data from HTML.');
                }
            })
            .catch(error => console.error('Error retrieving process data:', error));
    }

    // Sidebar main navigation links click event handlers
    const links = document.querySelectorAll('.load-content');
    if (links) {
        links.forEach(link => {
            link.addEventListener('click', function(event) {
                event.preventDefault();

                // Show loading spinner
                loadingSpinner.style.display = 'flex';

                // Convert contentUrl to a URL object
                const url = new URL(this.getAttribute('data-content'), window.location.origin);
                url.searchParams.set('account_id', selectedAccountId);

                // Remove active class from all items
                document.querySelectorAll('#sidebar .list-group-item').forEach(item => {
                    item.classList.remove('active');
                });

                // Add active class to clicked item
                this.closest('.list-group-item').classList.add('active');

                // Fetch content using the updated URL
                fetch(url)
                    .then(response => {
                        if (!response.ok) {
                            throw new Error('Network response was not ok');
                        }
                        return response.text();
                    })
                    .then(html => {
                        contentPane.innerHTML = html;
                        initializeComponents();
                    })
                    .catch(error => console.error('Error loading content:', error))
                    .finally(() => {
                        // Hide loading spinner
                        loadingSpinner.style.display = 'none';
                    });
            });
        });

        // Simulate click on "Email Accounts" to load it on startup
        const emailAccountsItem = document.getElementById('email-accounts-item');
        if (emailAccountsItem) {
            emailAccountsItem.querySelector('.load-content').click();
        }
    }

    // Consolidated form submission event listeners
    document.addEventListener('submit', function(e) {
        if (e.target.matches('#emailAccountForm')) {
            e.preventDefault();
            // submitForm(e.target);
            const formData = new FormData(emailAccountForm);
            fetch(emailAccountForm.action, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': '{{ csrf_token() }}' // Ensure CSRF token is included
                    }
                })
                .then(response => response.text())
                .then(html => {
                    // Load the email accounts page content into the contentPane
                    document.getElementById('contentPane').innerHTML = html;
                })
                .catch(error => console.error('Error updating email account:', error));
        }

        if (e.target.matches('#wordsForm')) {
            e.preventDefault();
            submitForm(e.target, '/words');
        }

        if (e.target.matches('#datesForm')) {
            const startDate = document.getElementById('start_date').value;
            const endDate = document.getElementById('end_date').value;
            const limitDatesChecked = document.getElementById('limitDates').checked;

            // Update the navbar dropdown with the new dates only if the form is submitted
            const selectedOption = emailAccountSelect.querySelector(`option[value="${selectedAccountId}"]`);
            if (selectedOption) {
                const email = selectedOption.textContent.trim().split(' ')[0]; // Assuming email is the first part
                if (limitDatesChecked) {
                    selectedOption.textContent = `${email} (${startDate} - ${endDate})`;
                } else {
                    selectedOption.textContent = email; // Remove dates from the text
                }
            }

            e.preventDefault();
            submitForm(e.target, '/dates');
        }

        if (e.target.matches('#aiPromptsForm')) {
            e.preventDefault();
            submitForm(e.target, `/ai_prompts`);
        }

        if (e.target.matches('form[action^="/edit_job/"]')) {
            e.preventDefault();
            submitForm(e.target);
        }
    });

    // Function to simulate a click on an element
    function simulateClick(elementId) {
        const element = document.getElementById(elementId);
        if (element) {
            element.querySelector('.load-content').click();
        }
    }

    // Consolidated function for all click event listeners
    document.addEventListener('click', function(e) {
        const target = e.target;

        // Toggle Email State
        if (target.matches('.toggle-action')) {
            e.preventDefault();
            const addressId = target.getAttribute('data-email-id');
            const newState = target.getAttribute('data-action');
            toggleEmailState(addressId, newState, target);
        }

        // Cancel Add Account button
        if (target.matches('#cancelAddAccountButton')) {
            e.preventDefault();
            simulateClick('email-accounts-item');
        }

        // Handle View Account button
        if (target.matches('.view-account')) {
            e.preventDefault();
            const accountId = target.getAttribute('data-account-id');
            fetchContent(`/email_account_view/${accountId}`);
        }

        // Handle add Account button
        if (target.matches('#addAccountButton')) {
            e.preventDefault();
            fetchContent(`/email_account_add`);
        }

        // Handle Edit Account button
        if (target.matches('.edit-account')) {
            e.preventDefault();
            const accountId = target.getAttribute('data-account-id');
            fetchContent(`/email_account_edit/${accountId}`);
        }

        // Handle form submission for editing an account
        if (target.matches('#submitEditAccountButton')) {
            e.preventDefault();

            // Retrieve account_id from the data attribute
            const accountId = target.getAttribute('data-account-id');
            if (!accountId) {
                console.error('Account ID not found.');
                return;
            }
            const emailAccountForm = document.getElementById('emailAccountForm');
            const formData = new FormData(emailAccountForm);
            const csrfToken = document.querySelector('input[name="csrf_token"]').value;

            fetch(`/email_account_edit/${accountId}`, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': csrfToken
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        displayFlashMessage('Account updated.', 'success');
                        simulateClick('email-accounts-item');
                    } else {
                        displayFlashMessage('Account update failed: ' + (data.error || data.errors.join(', ')), 'danger');
                    }
                })
                .catch(error => {
                    displayFlashMessage('Account update failed: ' + error, 'danger');
                });
        }

        // Check if the delete button was clicked
        if (target.matches('#deleteAccountButton')) {
            e.preventDefault(); // Prevent default form submission

            const accountId = target.getAttribute('data-account-id');
            if (!accountId) {
                console.error('Account ID not found.');
                return;
            }

            const csrfToken = document.querySelector('input[name="csrf_token"]').value;

            // Show a confirmation dialog before proceeding
            const confirmDelete = confirm('Are you sure you want to delete this account?');
            if (!confirmDelete) return;

            // Perform the fetch request to delete the account
            fetch(`/email_account_delete/${accountId}`, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        displayFlashMessage('Email account deleted successfully!', 'success');
                        simulateClick('email-accounts-item');
                    } else {
                        displayFlashMessage('Failed to delete account: ' + data.error, 'danger');
                    }
                })
                .catch(error => {
                    displayFlashMessage('An error occurred while deleting the account.', 'danger');
                });
        }

        if (target.matches('#submitAddAccountButton')) {
            e.preventDefault();

            const emailAccountForm = document.getElementById('EmailAccountForm');
            const formData = new FormData(emailAccountForm);
            const csrfToken = document.querySelector('input[name="csrf_token"]').value; // Get CSRF token

            fetch('/email_account_add', {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': csrfToken
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        displayFlashMessage('Email account added successfully!', 'success');
                        selectedAccountId = data.account_id; // Set the new account as selected
                        simulateClick('email-accounts-item');
                    } else {
                        displayFlashMessage('Failed to add account: ' + data.error, 'danger');
                    }
                })
                .catch(error => {
                    displayFlashMessage('An error occurred while adding the account.', 'danger');
                });
        }

        if (target.matches('#closeViewAccountButton')) {
            e.preventDefault();
            simulateClick('email-accounts-item');
        }

        if (target.matches('#cancelEditAccountButton')) {
            e.preventDefault();
            simulateClick('email-accounts-item');
        }

        // Toggle State buttons
        if (target.matches('.toggle-state')) {
            e.preventDefault();
            const addressId = target.getAttribute('data-address-id');
            const newState = target.getAttribute('data-new-state');
            toggleEmailState(addressId, newState, target);
        }
    });

    // Function to toggle email state
    function toggleEmailState(addressId, newState, buttonElement) {
        fetch('/toggle_email_address_state', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('input[name="csrf_token"]').value
                },
                body: JSON.stringify({ address_id: addressId, new_state: newState })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const buttonGroup = buttonElement.closest('.btn-group');
                    buttonGroup.querySelectorAll('.btn').forEach(btn => {
                        btn.classList.remove('btn-success', 'btn-warning', 'btn-danger', 'btn-secondary');
                        btn.classList.add('btn-secondary');
                    });

                    if (newState === 'include') {
                        buttonElement.classList.remove('btn-secondary');
                        buttonElement.classList.add('btn-success');
                    } else if (newState === 'ignore') {
                        buttonElement.classList.remove('btn-secondary');
                        buttonElement.classList.add('btn-warning');
                    } else if (newState === 'exclude') {
                        buttonElement.classList.remove('btn-secondary');
                        buttonElement.classList.add('btn-danger');
                    }
                } else {
                    displayFlashMessage('Failed to update email address state: ' + data.message, 'danger');
                }
            })
            .catch(error => console.error('Error updating email address state:', error));
    }

    // Helper function to fetch content
    function fetchContent(url) {
        fetch(url)
            .then(response => response.text())
            .then(html => {
                document.getElementById('contentPane').innerHTML = html;
                initializeComponents(); // Re-initialize components
            })
            .catch(error => console.error(`Error loading content from ${url}:`, error));
    }

    // Helper function to submit forms via AJAX
    function submitForm(formElement, url = formElement.action) {
        const formData = new FormData(formElement);

        // Add selectedAccountId to the form data
        if (selectedAccountId) {
            formData.append('account_id', selectedAccountId);
        }

        fetch(url, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-CSRFToken': document.querySelector('input[name="csrf_token"]').value
                }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    if (data.message) {
                        displayFlashMessage(data.message, 'success');
                    }
                    // Optionally reload the page or update the UI
                    // location.reload();
                } else {
                    displayFlashMessage('Failed: ' + (data.error || data.errors.join(', ')), 'danger');
                }
            })
            .catch(error => console.error('Error submitting form:', error));
    }

    function displayFlashMessage(message, category) {
        const flashMessage = document.createElement('div');
        flashMessage.className = `alert alert-${category} alert-dismissible fade show`;
        flashMessage.textContent = message;
        flashMessage.innerHTML += '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';

        const flashContainer = document.querySelector('#flash-messages');
        flashContainer.appendChild(flashMessage);

        setTimeout(() => {
            flashMessage.classList.remove('show');
            setTimeout(() => flashMessage.remove(), 800);
        }, 5000);
    }

    // Helper function to test email connection
    function testEmailConnection(accountId) {
        const csrfToken = document.getElementById('csrf_token').value;

        fetch(`/test_email_connection/${accountId}`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken
                }
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    displayFlashMessage('Connection successful!', 'success');
                } else {
                    displayFlashMessage('Connection failed: ' + data.error, 'danger');
                }
            })
            .catch(error => {
                displayFlashMessage('Error testing connection: ' + error, 'danger');
                console.error('Error testing connection:', error);
            });
    }

    // Function to update folder/month email counts
    function updateEmailFolderTable() {
        const emailFolderTable = document.getElementById('emailFolderTable');
        if (emailFolderTable) {
            fetch(`/get_folder_counts/${selectedAccountId}`)
                .then(response => response.json())
                .then(data => {
                    const tbody = emailFolderTable.querySelector('tbody');
                    tbody.innerHTML = ''; // Clear existing rows
                    data.folders.forEach(item => {
                        let progress = 100;
                        if (item.email_count > 0) {
                            progress = (item.found_count / item.email_count) * 100;
                        }

                        const row = document.createElement('tr');
                        row.innerHTML = `
                        <td>${item.folder}</td>
                        <td>${item.email_count}</td>
                        <td>${item.found_count}</td>
                        <td>
                            <div class="progress">
                                <div class="progress-bar" role="progressbar" style="width: ${progress}%" aria-valuenow="${item.found_count}" aria-valuemin="0" aria-valuemax="${item.email_count}">
                                </div>
                            </div>
                        </td>
                    `;
                        tbody.appendChild(row);
                    });
                })
                .catch(error => {
                    displayFlashMessage('Error updating folder table: ' + error, 'danger');
                    console.error('Error updating folder table:', error);
                });
        }
    }

    // Function to check scan status
    function checkScanStatus(accountId) {
        fetch(`/check_scan_status/${accountId}`)
            .then(response => response.json())
            .then(data => {
                const runningIndicator = document.getElementById('runningIndicator');
                const stopButton = document.getElementById('stopButton');
                if (data.status === 'running') {
                    if (runningIndicator) {
                        runningIndicator.style.display = 'inline';
                        stopButton.style.display = 'inline';
                    }
                } else {
                    if (runningIndicator) {
                        runningIndicator.style.display = 'none';
                        stopButton.style.display = 'none';
                        // Disable live update toggle
                        liveUpdateToggle.checked = false;
                        liveUpdateToggle.dispatchEvent(new Event('change'));
                    }
                }
            })
            .catch(error => {
                displayFlashMessage('Error checking scan status: ' + error, 'danger');
                console.error('Error checking scan status:', error);
            });
    }

    function updateImapSettings() {
        const emailTypeSelect = document.getElementById('emailTypeSelect');
        const imapServer = document.getElementById('imapServer');
        const imapPort = document.getElementById('imapPort');
        const imapUseSsl = document.getElementById('imapUseSsl');

        if (emailTypeSelect.value === 'GMAIL') {
            imapServer.value = 'imap.gmail.com';
            imapPort.value = '993';
            imapUseSsl.value = 'y';
        } else if (emailTypeSelect.value === 'APPLE') {
            imapServer.value = 'imap.mail.me.com';
            imapPort.value = '993';
            imapUseSsl.value = 'y';
        }
    }

    // document.addEventListener('submit', function(e) {
    //     if (e.target.matches('#datesForm')) {
    //         e.preventDefault();
    //         const startDate = document.getElementById('start_date').value;
    //         const endDate = document.getElementById('end_date').value;
    //         const limitDatesChecked = document.getElementById('limitDates').checked;

    //         // Update the navbar dropdown with the new dates only if the form is submitted
    //         const selectedOption = emailAccountSelect.querySelector(`option[value="${selectedAccountId}"]`);
    //         if (selectedOption) {
    //             const email = selectedOption.textContent.trim().split(' ')[0]; // Assuming email is the first part
    //             if (limitDatesChecked) {
    //                 selectedOption.textContent = `${email} (${startDate} - ${endDate})`;
    //             } else {
    //                 selectedOption.textContent = email; // Remove dates from the text
    //             }
    //         }

    //         submitForm(e.target, '/dates');
    //     }
    // });

});